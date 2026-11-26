"""Logging safety tests."""

from __future__ import annotations

from dockit.core.logging import redact


def test_redact_removes_secret_values_recursively() -> None:
    value = {
        "authorization": "Bearer top-secret",
        "context": {"token": "also-secret", "safe": "kept"},
    }

    assert redact(value) == {
        "authorization": "[REDACTED]",
        "context": {"token": "[REDACTED]", "safe": "kept"},
    }
