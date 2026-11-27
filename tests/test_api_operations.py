"""Versioned API integration tests over the authoritative services."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dockit.api.app import create_app
from dockit.core.config import BASE_DIR, Environment, Settings
from dockit.services.security import AuthorizationService


class _Verifier:
    def verify(self, token: str) -> dict[str, object]:
        assert token == "access-token"
        return {"sub": "cognito-subject", "cognito:groups": ["drivers"]}


@pytest.fixture
def application(tmp_path: Path):
    database_path = tmp_path / "authoritative.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            (BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text(encoding="utf-8")
        )
    settings = Settings(
        environment=Environment.TEST,
        source_database_path=database_path,
        state_database_path=database_path,
        unified_authoritative_database=True,
        log_json=False,
    )
    return create_app(settings)


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer access-token"}


@contextmanager
def _authenticated_client(application) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(application) as client:
        with sqlite3.connect(application.state.settings.state_database_path) as connection:
            connection.execute(
                "INSERT INTO driver_identity_links (cognito_subject, driver_id, linked_at_utc) "
                "VALUES (?, ?, ?)",
                ("cognito-subject", "DRV006", "2026-08-04T04:00:00Z"),
            )
        application.state.authorization_service = AuthorizationService(
            application.state.state_connection,
            _Verifier(),
        )
        yield client


def test_protected_routes_return_standard_unauthorized_error(application) -> None:
    with _authenticated_client(application) as client:
        response = client.get("/api/v1/shipments")

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert response.json()["message"] == "bearer access token required"
    assert "correlation_id" in response.json()


def test_driver_shipment_list_is_scoped_and_paginated(application) -> None:
    with _authenticated_client(application) as client:
        response = client.get("/api/v1/shipments?limit=1", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["driver_id"] == "DRV006"
    assert payload["next_cursor"] is not None


def test_options_delay_proposal_hold_confirm_and_cancel_contracts(application) -> None:
    with _authenticated_client(application) as client:
        delay = client.post(
            "/api/v1/shipments/SHP1006/delays",
            headers=_headers(),
            json={"reported_delay_minutes": 45, "reason_code": "TRAFFIC"},
        )
        options = client.get("/api/v1/shipments/SHP1006/options", headers=_headers())
        option_rows = options.json()["options"]
        selectable_slots = [
            row["slot_id"]
            for row in option_rows
            if not row["requires_operations_approval"]
        ]
        assert len(selectable_slots) >= 2
        proposal = client.post(
            "/api/v1/shipments/SHP1006/proposals",
            headers=_headers(),
            json={"slot_id": selectable_slots[1]},
        )
        hold = client.post(
            "/api/v1/shipments/SHP1006/holds",
            headers=_headers(),
            json={
                "slot_id": selectable_slots[0],
                "options_observed_at_utc": options.json()["observed_at_utc"],
            },
        )
        hold_id = hold.json()["hold_id"]
        missing_idempotency = client.post(
            f"/api/v1/holds/{hold_id}/confirm",
            headers=_headers(),
        )
        confirmed = client.post(
            f"/api/v1/holds/{hold_id}/confirm",
            headers={**_headers(), "Idempotency-Key": "confirm-api-1"},
        )
        replay = client.post(
            f"/api/v1/holds/{hold_id}/confirm",
            headers={**_headers(), "Idempotency-Key": "confirm-api-1"},
        )
        second_hold = client.post(
            "/api/v1/shipments/SHP1006/holds",
            headers=_headers(),
            json={
                "slot_id": selectable_slots[1],
                "options_observed_at_utc": options.json()["observed_at_utc"],
            },
        )
        second_hold_id = second_hold.json()["hold_id"]
        cancelled = client.delete(
            f"/api/v1/holds/{second_hold_id}",
            headers=_headers(),
        )
        cancelled_replay = client.delete(
            f"/api/v1/holds/{second_hold_id}",
            headers=_headers(),
        )

    assert options.status_code == 200
    assert options.json()["shipment_id"] == "SHP1006"
    assert "status" in options.json()
    assert delay.status_code == 200
    assert delay.json()["shipment_id"] == "SHP1006"
    assert proposal.status_code == 200
    assert proposal.json()["status"] == "PENDING_DRIVER"
    assert hold.status_code == 200
    assert hold.json()["status"] == "ACTIVE"
    assert missing_idempotency.status_code == 428
    assert missing_idempotency.json()["code"] == "precondition_failed"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "RELEASED"
    assert cancelled_replay.status_code == 200
    assert cancelled_replay.json()["status"] == "RELEASED"


def test_chat_returns_service_unavailable_without_configured_runtime(application) -> None:
    with _authenticated_client(application) as client:
        response = client.post(
            "/api/v1/chat/thread-1/messages",
            headers=_headers(),
            json={"message": "show my status"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"


def test_hold_revalidates_scheduler_policy_and_option_age(application) -> None:
    with _authenticated_client(application) as client:
        options = client.get("/api/v1/shipments/SHP1006/options", headers=_headers()).json()
        observed_at = options["observed_at_utc"]
        invalid_slot = client.post(
            "/api/v1/shipments/SHP1006/holds",
            headers=_headers(),
            json={
                "slot_id": "SLOT-JAI-062",
                "options_observed_at_utc": observed_at,
            },
        )
        stale_at = (
            datetime.fromisoformat(observed_at).astimezone(UTC) - timedelta(minutes=6)
        ).isoformat()
        stale_options = client.post(
            "/api/v1/shipments/SHP1006/holds",
            headers=_headers(),
            json={
                "slot_id": options["options"][0]["slot_id"],
                "options_observed_at_utc": stale_at,
            },
        )

    assert invalid_slot.status_code == 409
    assert "feasible" in invalid_slot.json()["message"]
    assert stale_options.status_code == 409
    assert stale_options.json()["message"] == "the selected options have expired"


def test_openapi_lists_versioned_operational_and_chat_routes(application) -> None:
    with TestClient(application) as client:
        schema = client.get("/openapi.json").json()

    paths = schema["paths"]
    assert "/api/v1/shipments" in paths
    assert "/api/v1/shipments/{shipment_id}/options" in paths
    assert "/api/v1/holds/{hold_id}/confirm" in paths
    assert "/api/v1/chat/{thread_id}/messages" in paths
    validation_schema = paths["/api/v1/shipments/{shipment_id}/holds"]["post"]["responses"]["422"]
    assert "ErrorResponse" in str(validation_schema)
