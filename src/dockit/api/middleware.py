"""Cross-cutting HTTP middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from dockit.core.logging import correlation_id

LOGGER = logging.getLogger("dockit.http")
CORRELATION_ID_HEADER = "X-Correlation-ID"
RequestHandler = Callable[[Request], Awaitable[Response]]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Attach a trusted-or-generated identifier to each request and response."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_correlation_id = _correlation_id_from(request.headers.get(CORRELATION_ID_HEADER))
        token = correlation_id.set(request_correlation_id)
        started_at = perf_counter()
        try:
            response = await call_next(request)
            response.headers[CORRELATION_ID_HEADER] = request_correlation_id
            LOGGER.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1_000, 2),
                },
            )
            return response
        finally:
            correlation_id.reset(token)


def _correlation_id_from(header_value: str | None) -> str:
    if header_value:
        try:
            return str(UUID(header_value))
        except ValueError:
            pass
    return str(uuid4())
