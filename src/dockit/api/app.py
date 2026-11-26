"""Application factory for the DockIt HTTP service."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from dockit import __version__
from dockit.api.middleware import CorrelationIdMiddleware
from dockit.api.routes.system import router as system_router
from dockit.core.config import Settings, get_settings
from dockit.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own process-level resources; dependency clients are added in later steps."""

    logging.getLogger("dockit.application").info("application_started")
    yield
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
