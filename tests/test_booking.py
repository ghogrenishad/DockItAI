"""Authoritative booking transaction tests for a unified SetuHaul/DockIt database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dockit.core.config import BASE_DIR
from dockit.db.connection import connect_state_database
from dockit.db.migrations import MigrationRunner
from dockit.db.state_repository import StateRepository
from dockit.services.booking import BookingConflict, BookingWorkflow


def _authoritative_connection(tmp_path: Path):
    database_path = tmp_path / "authoritative.db"
    with sqlite3.connect(database_path) as database:
        database.executescript(
            (BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text(encoding="utf-8")
        )
    connection = connect_state_database(
        database_path,
        source_database_path=database_path,
        unified_authoritative_database=True,
    )
    MigrationRunner(BASE_DIR / "migrations").apply(connection)
    return connection


def _create_hold(connection) -> tuple[str, str]:
    slot_id = str(
        connection.execute(
            """
            SELECT slot_id FROM v_slot_availability
            WHERE facility_id = 'FAC-JAI-01' AND availability_status = 'AVAILABLE'
            ORDER BY slot_start_ts, dock_code, slot_id
            LIMIT 1
            """
        ).fetchone()[0]
    )
    StateRepository(connection).create_slot_hold(
        hold_id="hold-booking-1",
        source_shipment_id="SHP1006",
        source_slot_id=slot_id,
        actor_subject="driver:DRV006",
        conversation_reference_id=None,
        created_at_utc="2026-08-04T04:05:00Z",
        options_provided_at_utc="2026-08-04T04:05:00Z",
        expires_at_utc="2026-08-04T04:10:00Z",
    )
    return "hold-booking-1", slot_id


def test_confirmation_replaces_source_appointment_and_is_idempotent(tmp_path: Path) -> None:
    connection = _authoritative_connection(tmp_path)
    try:
        hold_id, slot_id = _create_hold(connection)
        workflow = BookingWorkflow(connection)

        result = workflow.confirm_held_option(
            hold_id=hold_id,
            actor_subject="driver:DRV006",
            idempotency_key="confirm-001",
            request_fingerprint="fingerprint-001",
            confirmed_at=datetime(2026, 8, 4, 4, 6, tzinfo=UTC),
        )

        assert result.slot_id == slot_id
        assert result.idempotent_replay is False
        assert connection.execute(
            "SELECT appointment_status, is_current FROM appointments WHERE appointment_id = ?",
            (result.appointment_id,),
        ).fetchone() == ("CONFIRMED", 1)
        assert connection.execute(
            "SELECT appointment_status, is_current FROM appointments "
            "WHERE appointment_id = 'APT1006'"
        ).fetchone() == ("CANCELLED", 0)
        assert connection.execute(
            "SELECT status FROM slot_holds WHERE hold_id = ?", (hold_id,)
        ).fetchone() == ("RELEASED",)
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (1,)

        replay = workflow.confirm_held_option(
            hold_id=hold_id,
            actor_subject="driver:DRV006",
            idempotency_key="confirm-001",
            request_fingerprint="fingerprint-001",
            confirmed_at=datetime(2026, 8, 4, 4, 7, tzinfo=UTC),
        )
        assert replay == result.__class__(result.appointment_id, slot_id, True)
        assert connection.execute(
            "SELECT COUNT(*) FROM appointments WHERE shipment_id = 'SHP1006' AND is_current = 1"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_confirmation_revalidates_slot_and_idempotency_fingerprint(tmp_path: Path) -> None:
    connection = _authoritative_connection(tmp_path)
    try:
        hold_id, slot_id = _create_hold(connection)
        connection.execute(
            """
            INSERT INTO appointments (
                appointment_id, shipment_id, slot_id, appointment_status, booking_source,
                is_current, booked_at, confirmed_at, updated_at
            ) VALUES ('APT-OTHER', 'SHP1008', ?, 'CONFIRMED', 'PLANNER', 1, ?, ?, ?)
            """,
            (slot_id, "2026-08-04T04:05:30Z", "2026-08-04T04:05:30Z", "2026-08-04T04:05:30Z"),
        )
        workflow = BookingWorkflow(connection)
        with pytest.raises(BookingConflict, match="no longer available"):
            workflow.confirm_held_option(
                hold_id=hold_id,
                actor_subject="driver:DRV006",
                idempotency_key="confirm-002",
                request_fingerprint="fingerprint-002",
                confirmed_at=datetime(2026, 8, 4, 4, 6, tzinfo=UTC),
            )
    finally:
        connection.close()
