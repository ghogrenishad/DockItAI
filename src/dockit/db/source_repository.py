"""Read-only driver-scoped queries over the confirmed SetuHaul snapshot."""

from __future__ import annotations

import sqlite3

from dockit.db.connection import SourceCatalog, SourceDatabaseConnection

type SourceValue = int | str | None
type SourceRecord = dict[str, SourceValue]


class SourceContractError(RuntimeError):
    """Raised when the SetuHaul snapshot lacks the required driver read contract."""


_SNAPSHOT_COLUMNS = frozenset(
    {
        "shipment_id",
        "driver_id",
        "destination_facility_id",
        "priority_code",
        "required_dock_type",
        "current_status",
        "effective_eta_ts",
        "appointment_id",
        "slot_id",
        "slot_start_ts",
        "slot_end_ts",
        "planned_dock_code",
        "gate_in_ts",
        "queue_state",
        "queue_position",
    }
)

_DRIVER_SHIPMENT_SUMMARY = """
    SELECT
        shipment_id,
        driver_id,
        destination_facility_id,
        priority_code,
        required_dock_type,
        current_status,
        effective_eta_ts,
        appointment_id,
        slot_id,
        slot_start_ts,
        slot_end_ts,
        planned_dock_code,
        gate_in_ts,
        queue_state,
        queue_position
    FROM v_inbound_operational_state
"""


class SourceRepository:
    """Expose only driver-owned shipment summaries and snapshots."""

    def __init__(self, connection: SourceDatabaseConnection) -> None:
        self._connection = connection
        self.validate_contract()

    def validate_contract(self) -> None:
        """Fail fast if the source lacks the confirmed snapshot view or fields."""

        catalog = SourceCatalog(self._connection)
        if "v_inbound_operational_state" not in catalog.view_names():
            raise SourceContractError("source relation is missing: v_inbound_operational_state")
        actual_columns = {
            column.name for column in catalog.columns("v_inbound_operational_state")
        }
        missing_columns = sorted(_SNAPSHOT_COLUMNS - actual_columns)
        if missing_columns:
            raise SourceContractError(
                "source relation v_inbound_operational_state is missing columns: "
                f"{', '.join(missing_columns)}"
            )

    def list_driver_shipment_summaries(self, driver_id: str) -> tuple[SourceRecord, ...]:
        """List one driver's assigned shipments by effective ETA then shipment ID."""

        rows = self._connection.execute(
            _DRIVER_SHIPMENT_SUMMARY
            + """
            WHERE driver_id = ?
            ORDER BY effective_eta_ts, shipment_id
            """,
            (driver_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def get_driver_shipment_snapshot(
        self,
        driver_id: str,
        shipment_id: str,
    ) -> SourceRecord | None:
        """Return one owned shipment's ETA and current appointment/slot state."""

        row = self._connection.execute(
            _DRIVER_SHIPMENT_SUMMARY
            + """
            WHERE driver_id = ? AND shipment_id = ?
            """,
            (driver_id, shipment_id),
        ).fetchone()
        return _record(row) if row is not None else None


def _record(row: sqlite3.Row) -> SourceRecord:
    return {name: row[name] for name in row.keys()}
