"""Persistence primitives for DockIt-owned workflow state."""

from __future__ import annotations

from dockit.db.connection import StateDatabaseConnection, is_state_database_connection


class StateRepository:
    """Write DockIt state without reaching into the SetuHaul source database.

    These methods deliberately do not open transactions. A future workflow service
    owns the ``BEGIN IMMEDIATE`` scope so a hold, idempotency result, and audit event
    can commit atomically.
    """

    def __init__(self, connection: StateDatabaseConnection) -> None:
        if not is_state_database_connection(connection):
            raise ValueError("state repository requires a DockIt state database connection")
        self._connection = connection

    def create_conversation_reference(
        self,
        *,
        conversation_reference_id: str,
        principal_subject: str,
        source_thread_id: str | None,
        source_shipment_id: str | None,
        created_at_utc: str,
        last_seen_at_utc: str,
    ) -> None:
        """Persist a DockIt-to-source conversation reference without copying messages."""

        self._connection.execute(
            """
            INSERT INTO conversation_references (
                conversation_reference_id, principal_subject, source_thread_id,
                source_shipment_id, created_at_utc, last_seen_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_reference_id,
                principal_subject,
                source_thread_id,
                source_shipment_id,
                created_at_utc,
                last_seen_at_utc,
            ),
        )

    def create_slot_hold(
        self,
        *,
        hold_id: str,
        source_shipment_id: str,
        source_slot_id: str,
        actor_subject: str,
        conversation_reference_id: str | None,
        created_at_utc: str,
        options_provided_at_utc: str,
        expires_at_utc: str,
    ) -> None:
        """Record a local, unconfirmed hold for later workflow revalidation."""

        self._connection.execute(
            """
            INSERT INTO slot_holds (
                hold_id, source_shipment_id, source_slot_id, actor_subject,
                conversation_reference_id, status, created_at_utc,
                options_provided_at_utc, expires_at_utc
            ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
            """,
            (
                hold_id,
                source_shipment_id,
                source_slot_id,
                actor_subject,
                conversation_reference_id,
                created_at_utc,
                options_provided_at_utc,
                expires_at_utc,
            ),
        )

    def record_hold_reminder(
        self,
        *,
        hold_id: str,
        reminder_sent_at_utc: str,
    ) -> bool:
        """Record one sent chat reminder; workflow code owns deadline validation."""

        result = self._connection.execute(
            """
            UPDATE slot_holds
            SET reminder_sent_at_utc = ?
            WHERE hold_id = ?
              AND status = 'ACTIVE'
              AND reminder_sent_at_utc IS NULL
            """,
            (reminder_sent_at_utc, hold_id),
        )
        return result.rowcount == 1

    def record_idempotency_key(
        self,
        *,
        actor_subject: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        created_at_utc: str,
    ) -> None:
        """Persist a request identity; response semantics are defined in Step 3."""

        self._connection.execute(
            """
            INSERT INTO idempotency_records (
                actor_subject, operation, idempotency_key, request_fingerprint,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (actor_subject, operation, idempotency_key, request_fingerprint, created_at_utc),
        )

    def append_audit_event(
        self,
        *,
        audit_event_id: str,
        occurred_at_utc: str,
        actor_subject: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        correlation_id: str | None,
        payload_json: str,
        state_hold_id: str | None = None,
    ) -> None:
        """Append an immutable audit event, optionally tied to a local hold."""

        self._connection.execute(
            """
            INSERT INTO audit_events (
                audit_event_id, occurred_at_utc, actor_subject, event_type, entity_type,
                entity_id, correlation_id, payload_json, state_hold_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_event_id,
                occurred_at_utc,
                actor_subject,
                event_type,
                entity_type,
                entity_id,
                correlation_id,
                payload_json,
                state_hold_id,
            ),
        )
