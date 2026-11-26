"""Transactional local workflow primitives preceding the authoritative booking call."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from dockit.db.connection import StateDatabaseConnection, immediate_transaction


class WorkflowConflict(RuntimeError):
    """Raised when an operational command cannot safely change workflow state."""


@dataclass(frozen=True)
class HoldResult:
    hold_id: str
    expires_at_utc: str


class OperationalWorkflow:
    """Make holds, proposals, delay reports, expiry, and audit writes atomic."""

    def __init__(self, connection: StateDatabaseConnection) -> None:
        if not connection.unified_authoritative_database:
            raise ValueError("operational workflow requires the unified authoritative database")
        self._connection = connection

    def report_delay(
        self,
        *,
        shipment_id: str,
        actor_subject: str,
        declared_eta: datetime,
        reported_at: datetime,
        reason_code: str | None = None,
    ) -> str:
        """Persist a driver-declared ETA and its audit event in one transaction."""

        eta = _utc_text(declared_eta)
        reported = _utc_text(reported_at)
        with immediate_transaction(self._connection):
            if self._connection.execute(
                "SELECT 1 FROM shipments WHERE shipment_id = ?", (shipment_id,)
            ).fetchone() is None:
                raise WorkflowConflict("shipment does not exist")
            update_id = f"ETA-DKT-{uuid4().hex}"
            self._connection.execute(
                """
                INSERT INTO eta_updates (
                    eta_update_id, shipment_id, source_type, declared_eta_ts,
                    confidence_code, delay_reason_code, created_at
                ) VALUES (?, ?, 'DRIVER_DECLARED', ?, 'HIGH', ?, ?)
                """,
                (update_id, shipment_id, eta, reason_code, reported),
            )
            self._audit(actor_subject, "delay_reported", "shipment", shipment_id, reported, None)
            return update_id

    def create_hold(
        self,
        *,
        shipment_id: str,
        slot_id: str,
        actor_subject: str,
        options_provided_at: datetime,
    ) -> HoldResult:
        """Revalidate an open source slot, then create the confirmed five-minute local hold."""

        created = _utc_text(options_provided_at)
        expires = _utc_text(options_provided_at.astimezone(UTC) + timedelta(minutes=5))
        with immediate_transaction(self._connection):
            occupied = self._connection.execute(
                """
                SELECT 1 FROM appointments
                WHERE slot_id = ?
                  AND appointment_status IN ('PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS')
                """,
                (slot_id,),
            ).fetchone()
            open_slot = self._connection.execute(
                "SELECT 1 FROM appointment_slots WHERE slot_id = ? AND slot_status = 'OPEN'",
                (slot_id,),
            ).fetchone()
            if occupied is not None or open_slot is None:
                raise WorkflowConflict("slot is no longer available")
            hold_id = f"HOLD-DKT-{uuid4().hex}"
            self._connection.execute(
                """
                INSERT INTO slot_holds (
                    hold_id, source_shipment_id, source_slot_id, actor_subject, status,
                    created_at_utc, options_provided_at_utc, expires_at_utc
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (hold_id, shipment_id, slot_id, actor_subject, created, created, expires),
            )
            self._audit(actor_subject, "hold_created", "slot_hold", hold_id, created, hold_id)
            return HoldResult(hold_id, expires)

    def create_proposal(
        self,
        *,
        shipment_id: str,
        slot_id: str,
        actor_subject: str,
        created_at: datetime,
        requires_warehouse_approval: bool,
        requires_affected_driver_approval: bool,
    ) -> str:
        """Persist a non-booking proposal for user choice or required approvals."""

        timestamp = _utc_text(created_at)
        proposal_id = f"PROP-DKT-{uuid4().hex}"
        with immediate_transaction(self._connection):
            self._connection.execute(
                """
                INSERT INTO booking_proposals (
                    proposal_id, shipment_id, slot_id, proposal_status,
                    requires_warehouse_approval, requires_affected_driver_approval, created_at_utc
                ) VALUES (?, ?, ?, 'PENDING_DRIVER', ?, ?, ?)
                """,
                (
                    proposal_id,
                    shipment_id,
                    slot_id,
                    int(requires_warehouse_approval),
                    int(requires_affected_driver_approval),
                    timestamp,
                ),
            )
            self._audit(
                actor_subject,
                "proposal_created",
                "booking_proposal",
                proposal_id,
                timestamp,
                None,
            )
        return proposal_id

    def release_or_expire_holds(
        self,
        *,
        at: datetime,
        actor_subject: str = "system",
    ) -> tuple[str, ...]:
        """Release every expired active hold and append one audit event per hold."""

        timestamp = _utc_text(at)
        with immediate_transaction(self._connection):
            rows = self._connection.execute(
                "SELECT hold_id FROM slot_holds WHERE status = 'ACTIVE' AND expires_at_utc <= ?",
                (timestamp,),
            ).fetchall()
            hold_ids = tuple(str(row[0]) for row in rows)
            for hold_id in hold_ids:
                self._connection.execute(
                    "UPDATE slot_holds SET status = 'EXPIRED', finalized_at_utc = ? "
                    "WHERE hold_id = ?",
                    (timestamp, hold_id),
                )
                self._audit(actor_subject, "hold_expired", "slot_hold", hold_id, timestamp, hold_id)
            return hold_ids

    def _audit(
        self,
        actor_subject: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        occurred_at: str,
        hold_id: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events (
                audit_event_id, occurred_at_utc, actor_subject, event_type,
                entity_type, entity_id, payload_json, state_hold_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"AUD-DKT-{uuid4().hex}",
                occurred_at,
                actor_subject,
                event_type,
                entity_type,
                entity_id,
                json.dumps({}),
                hold_id,
            ),
        )


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("workflow timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
