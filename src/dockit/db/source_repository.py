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

_SCHEDULING_COLUMNS = {
    "appointment_slots": frozenset(
        {"slot_id", "facility_id", "dock_id", "slot_start_ts", "slot_end_ts", "slot_status"}
    ),
    "appointments": frozenset(
        {"appointment_id", "shipment_id", "slot_id", "appointment_status", "is_current"}
    ),
    "dock_status_events": frozenset(
        {"dock_id", "event_type", "event_start_ts", "event_end_ts"}
    ),
    "docks": frozenset(
        {
            "dock_id",
            "facility_id",
            "dock_code",
            "dock_type",
            "supports_refrigerated",
            "max_vehicle_weight_kg",
        }
    ),
    "facilities": frozenset(
        {
            "facility_id",
            "timezone",
            "open_time",
            "close_time",
            "default_unload_min",
            "active_flag",
        }
    ),
    "facility_checkins": frozenset(
        {"shipment_id", "queue_state", "gate_in_ts", "unload_start_ts", "actual_dock_id"}
    ),
    "facility_rules": frozenset({"facility_id", "rule_type", "rule_value", "active_flag"}),
    "shipments": frozenset(
        {
            "shipment_id",
            "destination_facility_id",
            "vehicle_id",
            "load_weight_kg",
            "required_dock_type",
            "temperature_control_required",
            "priority_code",
            "expected_unload_min",
            "current_status",
        }
    ),
    "v_latest_eta": frozenset({"shipment_id", "effective_eta_ts", "eta_confidence"}),
    "vehicles": frozenset({"vehicle_id", "capacity_kg", "refrigeration_capable"}),
}


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

    def get_shipment_facility(self, shipment_id: str) -> str | None:
        """Return the facility for one shipment for internal scheduling revalidation."""

        self._validate_scheduling_contract()
        row = self._connection.execute(
            "SELECT destination_facility_id FROM shipments WHERE shipment_id = ?",
            (shipment_id,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    def get_scheduling_facility(self, facility_id: str) -> SourceRecord | None:
        """Return the local time and operating hours for one scheduling facility."""

        self._validate_scheduling_contract()
        row = self._connection.execute(
            """
            SELECT facility_id, timezone, open_time, close_time, default_unload_min
            FROM facilities
            WHERE facility_id = ? AND active_flag = 1
            """,
            (facility_id,),
        ).fetchone()
        return _record(row) if row is not None else None

    def list_scheduling_shipments(self, facility_id: str) -> tuple[SourceRecord, ...]:
        """Return active facility shipments with ETA, vehicle, and queue facts."""

        self._validate_scheduling_contract()
        rows = self._connection.execute(
            """
            SELECT
                shipment.shipment_id,
                shipment.destination_facility_id,
                shipment.load_weight_kg,
                shipment.required_dock_type,
                shipment.temperature_control_required,
                shipment.priority_code,
                shipment.expected_unload_min,
                shipment.current_status,
                eta.effective_eta_ts,
                eta.eta_confidence,
                vehicle.capacity_kg AS vehicle_capacity_kg,
                vehicle.refrigeration_capable,
                checkin.queue_state,
                checkin.gate_in_ts,
                checkin.unload_start_ts,
                checkin.actual_dock_id
            FROM shipments AS shipment
            INNER JOIN v_latest_eta AS eta ON eta.shipment_id = shipment.shipment_id
            INNER JOIN vehicles AS vehicle ON vehicle.vehicle_id = shipment.vehicle_id
            LEFT JOIN facility_checkins AS checkin ON checkin.shipment_id = shipment.shipment_id
            WHERE shipment.destination_facility_id = ?
              AND shipment.current_status NOT IN ('COMPLETED', 'CANCELLED')
            ORDER BY eta.effective_eta_ts, shipment.shipment_id
            """,
            (facility_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def list_scheduling_slots(self, facility_id: str) -> tuple[SourceRecord, ...]:
        """Return every facility slot, its dock capability, and active occupancy."""

        self._validate_scheduling_contract()
        rows = self._connection.execute(
            """
            SELECT
                slot.slot_id,
                slot.facility_id,
                slot.dock_id,
                dock.dock_code,
                dock.dock_type,
                dock.supports_refrigerated,
                dock.max_vehicle_weight_kg,
                slot.slot_start_ts,
                slot.slot_end_ts,
                slot.slot_status,
                appointment.appointment_id,
                appointment.shipment_id AS occupied_by_shipment_id,
                appointment.appointment_status
            FROM appointment_slots AS slot
            INNER JOIN docks AS dock ON dock.dock_id = slot.dock_id
            LEFT JOIN appointments AS appointment
                ON appointment.slot_id = slot.slot_id
               AND appointment.appointment_status IN (
                    'PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS'
               )
            WHERE slot.facility_id = ?
            ORDER BY slot.slot_start_ts, dock.dock_code, slot.slot_id
            """,
            (facility_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def list_scheduling_appointments(self, facility_id: str) -> tuple[SourceRecord, ...]:
        """Return active appointments that a future optimizer may keep fixed or propose moving."""

        self._validate_scheduling_contract()
        rows = self._connection.execute(
            """
            SELECT
                appointment.appointment_id,
                appointment.shipment_id,
                appointment.slot_id,
                appointment.appointment_status,
                shipment.priority_code,
                shipment.expected_unload_min,
                eta.effective_eta_ts,
                eta.eta_confidence
            FROM appointments AS appointment
            INNER JOIN shipments AS shipment ON shipment.shipment_id = appointment.shipment_id
            INNER JOIN appointment_slots AS slot ON slot.slot_id = appointment.slot_id
            INNER JOIN v_latest_eta AS eta ON eta.shipment_id = shipment.shipment_id
            WHERE slot.facility_id = ?
              AND appointment.is_current = 1
              AND appointment.appointment_status IN (
                    'PENDING_CONFIRMATION', 'CONFIRMED', 'IN_PROGRESS'
              )
            ORDER BY slot.slot_start_ts, appointment.appointment_id
            """,
            (facility_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def list_scheduling_rules(self, facility_id: str) -> tuple[SourceRecord, ...]:
        """Return active facility rules without deciding their policy meaning."""

        self._validate_scheduling_contract()
        rows = self._connection.execute(
            """
            SELECT rule_type, rule_value
            FROM facility_rules
            WHERE facility_id = ? AND active_flag = 1
            ORDER BY rule_type
            """,
            (facility_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def list_scheduling_dock_events(self, facility_id: str) -> tuple[SourceRecord, ...]:
        """Return facility dock events; the evaluator determines their slot impact."""

        self._validate_scheduling_contract()
        rows = self._connection.execute(
            """
            SELECT event.dock_id, event.event_type, event.event_start_ts, event.event_end_ts
            FROM dock_status_events AS event
            INNER JOIN docks AS dock ON dock.dock_id = event.dock_id
            WHERE dock.facility_id = ?
            ORDER BY event.event_start_ts, event.dock_event_id
            """,
            (facility_id,),
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def _validate_scheduling_contract(self) -> None:
        catalog = SourceCatalog(self._connection)
        relations = set(catalog.relation_names())
        for relation, required_columns in _SCHEDULING_COLUMNS.items():
            if relation not in relations:
                raise SourceContractError(f"source scheduling relation is missing: {relation}")
            actual_columns = {column.name for column in catalog.columns(relation)}
            missing_columns = sorted(required_columns - actual_columns)
            if missing_columns:
                raise SourceContractError(
                    f"source scheduling relation {relation} is missing columns: "
                    f"{', '.join(missing_columns)}"
                )


def _record(row: sqlite3.Row) -> SourceRecord:
    return {name: row[name] for name in row.keys()}
