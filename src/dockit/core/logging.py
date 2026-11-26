"""Structured logging helpers that avoid emitting secrets."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_SENSITIVE_FIELD_MARKERS = ("authorization", "credential", "password", "secret", "token")
_REQUEST_FIELDS = ("event", "method", "path", "status_code", "duration_ms")


def redact(value: Any, field_name: str = "") -> Any:
    """Remove likely credential values from structured log data."""

    if any(marker in field_name.lower() for marker in _SENSITIVE_FIELD_MARKERS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {key: redact(item, key) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Format the small, explicit request event schema as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        for field_name in _REQUEST_FIELDS:
            if hasattr(record, field_name):
                payload[field_name] = redact(getattr(record, field_name), field_name)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str, *, json_output: bool) -> None:
    """Configure DockIt logging without changing application log semantics."""

    handler = logging.StreamHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    logger = logging.getLogger("dockit")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
