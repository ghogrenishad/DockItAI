"""Read-only conversion from SetuHaul source records into pure scheduling facts."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from dockit.db.source_repository import SourceRecord, SourceRepository
from dockit.scheduler.models import (
    ActiveAppointment,
    DockEvent,
    FacilitySchedule,
    PriorityCode,
    SchedulingShipment,
    SchedulingSlot,
    SchedulingSnapshot,
    SlotAvailability,
)

_WAITING_STATES = frozenset(
    {"WAITING_EARLY", "WAITING_LATE", "WAITING_DOCK_UNAVAILABLE", "CALLED_TO_DOCK"}
)


def load_scheduling_snapshot(
    repository: SourceRepository,
    facility_id: str,
    *,
    observed_at: datetime,
) -> SchedulingSnapshot:
    """Build a scheduling snapshot without giving the evaluator database access."""

    facility_row = repository.get_scheduling_facility(facility_id)
    if facility_row is None:
        raise KeyError(f"unknown active scheduling facility: {facility_id}")
    facility = _facility(facility_row)
    shipments = tuple(_shipment(row) for row in repository.list_scheduling_shipments(facility_id))
    slots = tuple(_slot(row) for row in repository.list_scheduling_slots(facility_id))
    appointments = tuple(
        _appointment(row) for row in repository.list_scheduling_appointments(facility_id)
    )
    events = tuple(_event(row) for row in repository.list_scheduling_dock_events(facility_id))
    return SchedulingSnapshot(
        facility=facility,
        shipments=shipments,
        slots=slots,
        appointments=appointments,
        dock_events=events,
        observed_at=observed_at.astimezone(UTC),
    )


def _facility(row: SourceRecord) -> FacilitySchedule:
    return FacilitySchedule(
        facility_id=_string(row, "facility_id"),
        timezone=ZoneInfo(_string(row, "timezone")),
        open_time=time.fromisoformat(_string(row, "open_time")),
        close_time=time.fromisoformat(_string(row, "close_time")),
        last_new_start_time=None,
        heavy_dock_required_kg=None,
    )


def with_facility_rules(
    snapshot: SchedulingSnapshot,
    rules: tuple[SourceRecord, ...],
) -> SchedulingSnapshot:
    """Apply source rule values after the profile is read, keeping parsing explicit."""

    values = {_string(rule, "rule_type"): _string(rule, "rule_value") for rule in rules}
    facility = FacilitySchedule(
        facility_id=snapshot.facility.facility_id,
        timezone=snapshot.facility.timezone,
        open_time=snapshot.facility.open_time,
        close_time=snapshot.facility.close_time,
        last_new_start_time=(
            time.fromisoformat(values["LAST_NEW_START_TIME"])
            if "LAST_NEW_START_TIME" in values
            else None
        ),
        heavy_dock_required_kg=(
            int(values["HEAVY_DOCK_REQUIRED_KG"])
            if "HEAVY_DOCK_REQUIRED_KG" in values
            else None
        ),
    )
    return SchedulingSnapshot(
        facility=facility,
        shipments=snapshot.shipments,
        slots=snapshot.slots,
        appointments=snapshot.appointments,
        dock_events=snapshot.dock_events,
        observed_at=snapshot.observed_at,
    )


def load_snapshot_with_rules(
    repository: SourceRepository,
    facility_id: str,
    *,
    observed_at: datetime,
) -> SchedulingSnapshot:
    """Load all confirmed source facts needed by the Step 4 evaluator."""

    snapshot = load_scheduling_snapshot(repository, facility_id, observed_at=observed_at)
    return with_facility_rules(snapshot, repository.list_scheduling_rules(facility_id))


def _shipment(row: SourceRecord) -> SchedulingShipment:
    queue_state = _optional_string(row, "queue_state")
    return SchedulingShipment(
        shipment_id=_string(row, "shipment_id"),
        facility_id=_string(row, "destination_facility_id"),
        priority=PriorityCode(_string(row, "priority_code")),
        required_dock_type=_string(row, "required_dock_type"),
        temperature_control_required=bool(_integer(row, "temperature_control_required")),
        load_weight_kg=_integer(row, "load_weight_kg"),
        expected_unload_minutes=_integer(row, "expected_unload_min"),
        eta_at=_timestamp(_string(row, "effective_eta_ts")),
        eta_confidence=_string(row, "eta_confidence"),
        is_waiting=queue_state in _WAITING_STATES,
        current_status=_string(row, "current_status"),
        unload_started_at=(
            _timestamp(unload_start)
            if (unload_start := _optional_string(row, "unload_start_ts")) is not None
            else None
        ),
        actual_dock_id=_optional_string(row, "actual_dock_id"),
    )


def _slot(row: SourceRecord) -> SchedulingSlot:
    slot_status = _string(row, "slot_status")
    appointment_id = _optional_string(row, "appointment_id")
    availability = (
        SlotAvailability.OCCUPIED
        if appointment_id is not None
        else SlotAvailability.AVAILABLE
        if slot_status == "OPEN"
        else SlotAvailability(slot_status)
    )
    return SchedulingSlot(
        slot_id=_string(row, "slot_id"),
        facility_id=_string(row, "facility_id"),
        dock_id=_string(row, "dock_id"),
        dock_code=_string(row, "dock_code"),
        dock_type=_string(row, "dock_type"),
        supports_refrigerated=bool(_integer(row, "supports_refrigerated")),
        max_vehicle_weight_kg=_integer(row, "max_vehicle_weight_kg"),
        starts_at=_timestamp(_string(row, "slot_start_ts")),
        ends_at=_timestamp(_string(row, "slot_end_ts")),
        availability=availability,
        appointment_id=appointment_id,
        occupied_by_shipment_id=_optional_string(row, "occupied_by_shipment_id"),
        appointment_status=_optional_string(row, "appointment_status"),
    )


def _appointment(row: SourceRecord) -> ActiveAppointment:
    return ActiveAppointment(
        appointment_id=_string(row, "appointment_id"),
        shipment_id=_string(row, "shipment_id"),
        slot_id=_string(row, "slot_id"),
        status=_string(row, "appointment_status"),
        priority=PriorityCode(_string(row, "priority_code")),
    )


def _event(row: SourceRecord) -> DockEvent:
    end = _optional_string(row, "event_end_ts")
    return DockEvent(
        dock_id=_string(row, "dock_id"),
        event_type=_string(row, "event_type"),
        starts_at=_timestamp(_string(row, "event_start_ts")),
        ends_at=_timestamp(end) if end is not None else None,
    )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("source timestamp must include an offset")
    return parsed.astimezone(UTC)


def _string(row: SourceRecord, name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise ValueError(f"source field {name} must be text")
    return value


def _optional_string(row: SourceRecord, name: str) -> str | None:
    value = row[name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"source field {name} must be text or null")
    return value


def _integer(row: SourceRecord, name: str) -> int:
    value = row[name]
    if not isinstance(value, int):
        raise ValueError(f"source field {name} must be an integer")
    return value
