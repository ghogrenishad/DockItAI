"""ASGI entrypoint for production servers and local development."""

from __future__ import annotations

import uvicorn

from dockit.api.app import create_app
from dockit.core.config import get_settings

app = create_app()


def run() -> None:
    """Run DockIt with the configured local bind address."""

    settings = get_settings()
    uvicorn.run(app, host=settings.server_host, port=settings.server_port)
