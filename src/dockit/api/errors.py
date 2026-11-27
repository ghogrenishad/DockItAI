"""Standard error responses for the versioned HTTP API."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from dockit.core.logging import correlation_id
from dockit.schemas.contracts import ErrorCode, ErrorResponse

LOGGER = logging.getLogger("dockit.api")


def _error_code(status_code: int) -> ErrorCode:
    if status_code in {400, 422}:
        return ErrorCode.VALIDATION_ERROR
    if status_code == 401:
        return ErrorCode.UNAUTHORIZED
    if status_code == 403:
        return ErrorCode.FORBIDDEN
    if status_code == 404:
        return ErrorCode.NOT_FOUND
    if status_code == 409:
        return ErrorCode.CONFLICT
    if status_code in {412, 428}:
        return ErrorCode.PRECONDITION_FAILED
    if status_code == 429:
        return ErrorCode.RATE_LIMITED
    if status_code == 503:
        return ErrorCode.SERVICE_UNAVAILABLE
    return ErrorCode.INTERNAL_ERROR


def _correlation_uuid() -> UUID | None:
    current = correlation_id.get()
    if current is None:
        return None
    try:
        return UUID(current)
    except ValueError:
        return None


def error_response(status_code: int, message: str) -> JSONResponse:
    """Build one stable error envelope without exposing internal exception details."""

    payload = ErrorResponse(
        code=_error_code(status_code),
        message=message,
        correlation_id=_correlation_uuid(),
    )
    return JSONResponse(payload.model_dump(mode="json"), status_code=status_code)


async def http_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Normalize explicit HTTP errors."""

    del request
    if isinstance(exception, StarletteHTTPException):
        message = exception.detail if isinstance(exception.detail, str) else "request failed"
        return error_response(exception.status_code, message)
    return error_response(500, "internal server error")


async def validation_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    """Normalize body/path/query validation failures without echoing input values."""

    del request
    if not isinstance(exception, RequestValidationError):
        return error_response(500, "internal server error")
    return error_response(422, "request validation failed")


async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    """Return a generic 500 while retaining diagnostic details only in logs."""

    del request
    LOGGER.exception("unhandled_api_error", exc_info=exception)
    return error_response(500, "internal server error")
