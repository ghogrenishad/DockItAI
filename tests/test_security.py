"""Cognito principal mapping and authorization tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dockit.core.config import BASE_DIR
from dockit.db.connection import connect_state_database
from dockit.db.migrations import MigrationRunner
from dockit.services.security import AuthorizationError, AuthorizationService


class _Verifier:
    def __init__(self, claims: dict[str, object]) -> None:
        self._claims = claims

    def verify(self, token: str) -> dict[str, object]:
        assert token == "access-token"
        return self._claims


def _connection(tmp_path: Path):
    path = tmp_path / "authoritative.db"
    with sqlite3.connect(path) as database:
        database.executescript((BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text())
    connection = connect_state_database(
        path,
        source_database_path=path,
        unified_authoritative_database=True,
    )
    MigrationRunner(BASE_DIR / "migrations").apply(connection)
    return connection


def test_driver_enrollment_uses_email_once_then_authorizes_by_subject(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        connection.execute(
            "UPDATE drivers SET email = ? WHERE driver_id = 'DRV006'",
            ("driver@example.com",),
        )
        service = AuthorizationService(
            connection,
            _Verifier({"sub": "cognito-subject", "cognito:groups": ["drivers"]}),
        )
        assert service.enroll_driver(
            subject="cognito-subject",
            email="driver@example.com",
            linked_at=datetime(2026, 8, 4, 4, 0, tzinfo=UTC),
        ) == "DRV006"
        principal = service.authenticate("access-token")
        principal.require_driver_ownership("DRV006")
        with pytest.raises(AuthorizationError, match="only on their linked"):
            principal.require_driver_ownership("DRV007")
    finally:
        connection.close()


def test_warehouse_group_cannot_impersonate_a_driver(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    try:
        service = AuthorizationService(
            connection,
            _Verifier({"sub": "warehouse-subject", "cognito:groups": ["warehouse"]}),
        )
        principal = service.authenticate("access-token")
        principal.require_group("warehouse")
        with pytest.raises(AuthorizationError, match="required Cognito group"):
            principal.require_driver_ownership("DRV006")
    finally:
        connection.close()
