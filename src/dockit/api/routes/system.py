"""Unauthenticated process and API metadata endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from dockit import __version__

router = APIRouter(tags=["system"])


@router.get("/api/v1", status_code=status.HTTP_200_OK)
async def api_root() -> dict[str, str]:
    """Identify the current versioned API surface."""

    return {"service": "dockit-ai", "version": __version__}


@router.get("/healthz", status_code=status.HTTP_200_OK)
async def health() -> dict[str, str]:
    """Liveness probe: the ASGI process can answer requests."""

    return {"status": "ok"}


@router.get("/readyz", status_code=status.HTTP_200_OK)
async def readiness(request: Request) -> dict[str, str]:
    """Readiness probe: startup configuration was parsed and validated."""

    _ = request.app.state.settings
    return {"status": "ready"}
