"""HTTP service-shell tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dockit.api.app import create_app
from dockit.core.config import BASE_DIR, Environment, Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    source_path = tmp_path / "setuhaul_source.db"
    schema_sql = (BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text(encoding="utf-8")

    with sqlite3.connect(source_path) as connection:
        connection.executescript(schema_sql)

    return Settings(
        environment=Environment.TEST,
        source_database_path=source_path,
        state_database_path=tmp_path / "dockit_state.db",
        log_json=False,
    )


def test_system_endpoints_are_available_and_versioned(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        assert client.get("/api/v1").json() == {"service": "dockit-ai", "version": "0.1.0"}
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready"}


def test_valid_correlation_id_is_returned_to_the_caller(test_settings: Settings) -> None:
    correlation_id = "123e4567-e89b-12d3-a456-426614174000"

    with TestClient(create_app(test_settings)) as client:
        response = client.get("/healthz", headers={"X-Correlation-ID": correlation_id})

    assert response.headers["X-Correlation-ID"] == correlation_id


def test_invalid_correlation_id_is_replaced(test_settings: Settings) -> None:
    with TestClient(create_app(test_settings)) as client:
        response = client.get("/healthz", headers={"X-Correlation-ID": "not-a-uuid"})

    assert response.headers["X-Correlation-ID"] != "not-a-uuid"
