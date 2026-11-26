"""HTTP service-shell tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from dockit.api.app import create_app
from dockit.core.config import Environment, Settings


def test_system_endpoints_are_available_and_versioned() -> None:
    settings = Settings(environment=Environment.TEST, log_json=False)

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1").json() == {"service": "dockit-ai", "version": "0.1.0"}
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}


def test_valid_correlation_id_is_returned_to_the_caller() -> None:
    settings = Settings(environment=Environment.TEST, log_json=False)
    correlation_id = "123e4567-e89b-12d3-a456-426614174000"

    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz", headers={"X-Correlation-ID": correlation_id})

    assert response.headers["X-Correlation-ID"] == correlation_id


def test_invalid_correlation_id_is_replaced() -> None:
    settings = Settings(environment=Environment.TEST, log_json=False)

    with TestClient(create_app(settings)) as client:
        response = client.get("/healthz", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.headers["X-Correlation-ID"] != "not-a-uuid"
