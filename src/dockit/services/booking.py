"""Atomic authoritative appointment confirmation after a driver selects a local hold."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from dockit.db.connection import StateDatabaseConnection, immediate_transaction
from dockit.services.slot_validation import SlotFeasibilityRevalidator, SlotNotFeasible


class BookingError(RuntimeError):
    """Base error for a booking workflow that did not commit."""


class BookingConflict(BookingError):
    """Raised when revalidation shows the confirmed booking cannot be made."""


class HoldExpired(BookingError):
    """Raised after releasing a hold whose five-minute deadline has elapsed."""


@dataclass(frozen=True)
class BookingResult:
    appointment_id: str
    slot_id: str
    idempotent_replay: bool


class BookingWorkflow:
    """Own one authoritative SQLite transaction; never accepts an LLM decision directly."""

    def __init__(
        self,
        connection: StateDatabaseConnection,
        slot_revalidator: SlotFeasibilityRevalidator | None = None,
    ) -> None:
        if not connection.unified_authoritative_database:
            raise ValueError("booking workflow requires the unified authoritative database")
        self._connection = connection
        self._slot_revalidator = slot_revalidator

    def confirm_held_option(
        self,
        *,
        hold_id: str,
        actor_subject: str,
        idempotency_key: str,
        request_fingerprint: str,
        confirmed_at: datetime,
    ) -> BookingResult:
        """Atomically revalidate, replace the appointment, release hold, and audit it."""

        timestamp = _utc_text(confirmed_at)
        with immediate_transaction(self._connection):
            replay = self._idempotent_result(
                actor_subject,
                "confirm_hold",
                idempotency_key,
                request_fingerprint,
            )
            if replay is not None:
                return replay

            hold = self._connection.execute(
                """
                SELECT source_shipment_id, source_slot_id, actor_subject, status, expires_at_utc
                FROM slot_holds
                WHERE hold_id = ?
                """,
                (hold_id,),
            ).fetchone()
            if hold is None:
                raise BookingConflict("hold does not exist")
            shipment_id, slot_id, owner, status, expires_at = (str(value) for value in hold)
            if owner != actor_subject:
                raise BookingConflict("actor does not own this hold")
            if status != "ACTIVE":
                raise BookingConflict("hold is not active")
            if timestamp >= expires_at:
                self._connection.execute(
                    """
                    UPDATE slot_holds
                    SET status = 'EXPIRED', finalized_at_utc = ?
                    WHERE hold_id = ?
                    """,
                    (timestamp, hold_id),
                )
                raise HoldExpired("hold expired before confirmation")

            slot = self._connection.execute(
                """
                SELECT slot_status
                FROM appointment_slots
                WHERE slot_id = ?
                """,
                (slot_id,),
            ).fetchone()
            if slot is None or slot[0] != "OPEN":
                raise BookingConflict("selected slot is no longer open")
            occupied = self._connection.execute(
                """
                SELECT appointment_id
                FROM appointments
                WHERE slot_id = ?
                  AND appointment_status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS')
                """,
                (slot_id,),
            ).fetchone()
            if occupied is not None:
                raise BookingConflict("selected slot is no longer available")
            if self._slot_revalidator is not None:
                try:
                    self._slot_revalidator.validate(shipment_id=shipment_id, slot_id=slot_id)
                except SlotNotFeasible as error:
                    raise BookingConflict(str(error)) from error

            current = self._connection.execute(
                """
                SELECT appointment_id, appointment_status
                FROM appointments
                WHERE shipment_id = ? AND is_current = 1
                  AND appointment_status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS')
                """,
                (shipment_id,),
            ).fetchone()
            if current is not None and current[1] == "IN_PROGRESS":
                raise BookingConflict("an in-progress appointment cannot be rescheduled")
            previous_appointment_id = str(current[0]) if current is not None else None
            if previous_appointment_id is not None:
                self._connection.execute(
                    """
                    UPDATE appointments
                    SET appointment_status = 'CANCELLED', is_current = 0,
                        cancelled_at = ?, cancellation_reason = ?, updated_at = ?
                    WHERE appointment_id = ?
                    """,
                    (
                        timestamp,
                        "Rescheduled after confirmed driver selection",
                        timestamp,
                        previous_appointment_id,
                    ),
                )

            appointment_id = f"APT-DKT-{uuid4().hex}"
            self._connection.execute(
                """
                INSERT INTO appointments (
                    appointment_id, shipment_id, slot_id, appointment_status, booking_source,
                    is_current, booked_at, confirmed_at, replaced_appointment_id, updated_at
                ) VALUES (?, ?, ?, 'CONFIRMED', 'DRIVER_CHAT', 1, ?, ?, ?, ?)
                """,
                (
                    appointment_id,
                    shipment_id,
                    slot_id,
                    timestamp,
                    timestamp,
                    previous_appointment_id,
                    timestamp,
                ),
            )
            self._connection.execute(
                """
                UPDATE slot_holds
                SET status = 'RELEASED', finalized_at_utc = ?
                WHERE hold_id = ?
                """,
                (timestamp, hold_id),
            )
            self._connection.execute(
                """
                INSERT INTO idempotency_records (
                    actor_subject, operation, idempotency_key, request_fingerprint,
                    created_at_utc, result_reference
                ) VALUES (?, 'confirm_hold', ?, ?, ?, ?)
                """,
                (actor_subject, idempotency_key, request_fingerprint, timestamp, appointment_id),
            )
            self._connection.execute(
                """
                INSERT INTO audit_events (
                    audit_event_id, occurred_at_utc, actor_subject, event_type,
                    entity_type, entity_id, payload_json, state_hold_id
                ) VALUES (?, ?, ?, 'appointment_confirmed', 'appointment', ?, ?, ?)
                """,
                (
                    f"AUD-DKT-{uuid4().hex}",
                    timestamp,
                    actor_subject,
                    appointment_id,
                    json.dumps({"shipment_id": shipment_id, "slot_id": slot_id}),
                    hold_id,
                ),
            )
            return BookingResult(appointment_id, slot_id, False)

    def _idempotent_result(
        self,
        actor_subject: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> BookingResult | None:
        row = self._connection.execute(
            """
            SELECT request_fingerprint, result_reference
            FROM idempotency_records
            WHERE actor_subject = ? AND operation = ? AND idempotency_key = ?
            """,
            (actor_subject, operation, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_fingerprint:
            raise BookingConflict("idempotency key was reused with another request")
        if row[1] is None:
            raise BookingConflict("idempotent command has no completed booking result")
        appointment = self._connection.execute(
            "SELECT slot_id FROM appointments WHERE appointment_id = ?",
            (row[1],),
        ).fetchone()
        if appointment is None:
            raise BookingConflict("idempotent booking result is missing")
        return BookingResult(str(row[1]), str(appointment[0]), True)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("confirmed_at must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
