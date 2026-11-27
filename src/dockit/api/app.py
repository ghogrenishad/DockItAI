"""Application factory for the DockIt HTTP service."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from dockit import __version__
from dockit.agent.orchestration import (
    AgentCoreChatModel,
    ChatModel,
    ConversationOrchestrator,
    OpenRouterChatModel,
    RedisConversationStore,
)
from dockit.api.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from dockit.api.middleware import (
    CorrelationIdMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from dockit.api.routes.operations import router as operations_router
from dockit.api.routes.system import router as system_router
from dockit.core.config import BASE_DIR, Settings, get_settings
from dockit.core.logging import configure_logging
from dockit.db.connection import connect_source_database, connect_state_database
from dockit.db.migrations import MigrationRunner
from dockit.db.source_repository import SourceRepository
from dockit.db.state_repository import StateRepository
from dockit.services.booking import BookingWorkflow
from dockit.services.security import AuthorizationService, CognitoAccessTokenVerifier
from dockit.services.slot_validation import SlotFeasibilityRevalidator
from dockit.services.workflows import OperationalWorkflow


def _conversation_orchestrator(settings: Settings) -> ConversationOrchestrator | None:
    """Create the chat boundary only when both durable state and inference are configured."""

    if not settings.redis_url or not settings.redis_token:
        return None
    if not settings.redis_url.startswith(("redis://", "rediss://")):
        return None

    store = RedisConversationStore(
        redis.Redis.from_url(
            settings.redis_url,
            password=settings.redis_token.get_secret_value(),
            decode_responses=True,
        ),
        ttl_seconds=settings.conversation_ttl_seconds,
    )
    if settings.agentcore_runtime_arn and settings.aws_region:
        import boto3  # type: ignore[import-untyped]

        model: ChatModel = AgentCoreChatModel(
            boto3.client("bedrock-agentcore", region_name=settings.aws_region),
            harness_arn=settings.agentcore_runtime_arn,
        )
    elif settings.openrouter_api_key:
        model = OpenRouterChatModel(
            api_key=settings.openrouter_api_key.get_secret_value(),
            model=settings.openrouter_model,
        )
    else:
        return None
    return ConversationOrchestrator(store, model)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open read-only source data and migrate DockIt-owned workflow state."""

    settings: Settings = app.state.settings
    source_connection, source_catalog = connect_source_database(settings.source_database_path)
    try:
        source_repository = SourceRepository(source_connection)
        state_connection = connect_state_database(
            settings.state_database_path,
            source_database_path=settings.source_database_path,
            unified_authoritative_database=settings.unified_authoritative_database,
        )
    except BaseException:
        source_connection.close()
        raise

    try:
        migrations = MigrationRunner(BASE_DIR / "migrations").apply(state_connection)
        app.state.source_catalog = source_catalog
        app.state.source_repository = source_repository
        app.state.state_connection = state_connection
        app.state.state_repository = StateRepository(state_connection)
        if settings.unified_authoritative_database:
            slot_revalidator = SlotFeasibilityRevalidator(source_repository)
            app.state.operational_workflow = OperationalWorkflow(
                state_connection,
                slot_revalidator,
            )
            app.state.booking_workflow = BookingWorkflow(state_connection, slot_revalidator)
        conversation_orchestrator = _conversation_orchestrator(settings)
        if conversation_orchestrator is not None:
            app.state.conversation_orchestrator = conversation_orchestrator
        if (
            settings.cognito_region
            and settings.cognito_user_pool_id
            and settings.cognito_app_client_id
        ):
            app.state.authorization_service = AuthorizationService(
                state_connection,
                CognitoAccessTokenVerifier(
                    region=settings.cognito_region,
                    user_pool_id=settings.cognito_user_pool_id,
                    app_client_id=settings.cognito_app_client_id,
                ),
            )
        logging.getLogger("dockit.application").info(
            "application_started",
            extra={"state_migrations_applied": len(migrations)},
        )
        yield
    finally:
        state_connection.close()
        source_connection.close()
        logging.getLogger("dockit.application").info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured app, allowing tests to inject safe settings directly."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level, json_output=resolved_settings.log_json)

    app = FastAPI(
        title="DockIt AI API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=resolved_settings.request_rate_limit_per_minute,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(system_router)
    app.include_router(operations_router, prefix="/api/v1")
    return app
