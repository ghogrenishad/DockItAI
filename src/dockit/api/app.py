"""Application factory for the DockIt HTTP service."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from dockit import __version__
from dockit.api.middleware import CorrelationIdMiddleware
from dockit.api.routes.system import router as system_router
from dockit.core.config import BASE_DIR, Settings, get_settings
from dockit.core.logging import configure_logging
from dockit.db.connection import connect_source_database, connect_state_database
from dockit.db.migrations import MigrationRunner
from dockit.db.source_repository import SourceRepository
from dockit.db.state_repository import StateRepository


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
        )
    except BaseException:
        source_connection.close()
        raise

    try:
        migrations = MigrationRunner(BASE_DIR / "migrations").apply(state_connection)
        app.state.source_catalog = source_catalog
        app.state.source_repository = source_repository
        app.state.state_repository = StateRepository(state_connection)
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
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(system_router)
    return app
