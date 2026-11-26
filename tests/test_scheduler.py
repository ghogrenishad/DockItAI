"""Deterministic feasibility and bounded CP-SAT proposal tests."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dockit.core.config import BASE_DIR
from dockit.db.connection import connect_source_database
from dockit.db.source_repository import SourceRepository
from dockit.scheduler.cp_sat import CpSatProposalOptimizer
from dockit.scheduler.feasibility import DeterministicFeasibility
from dockit.scheduler.models import (
    ActiveAppointment,
    DockEvent,
    FacilitySchedule,
    FeasibilityStatus,
    OptimizationStatus,
    PriorityCode,
    RejectionReason,
    SchedulingShipment,
    SchedulingSlot,
    SchedulingSnapshot,
    SlotAvailability,
)
from dockit.scheduler.source_adapter import load_snapshot_with_rules


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 4, hour, minute, tzinfo=UTC)


def _facility(*, last_start: time | None = None) -> FacilitySchedule:
    return FacilitySchedule(
        facility_id="FAC-1",
        timezone=ZoneInfo("Asia/Kolkata"),
        open_time=time(6),
        close_time=time(22),
        last_new_start_time=last_start,
        heavy_dock_required_kg=25_000,
    )


def _shipment(
    shipment_id: str = "SHP-1",
    *,
    priority: PriorityCode = PriorityCode.HIGH,
    eta_hour: int = 10,
    confidence: str = "HIGH",
    load_weight_kg: int = 10_000,
    duration: int = 60,
    dock_type: str = "STANDARD",
    refrigerated: bool = False,
    waiting: bool = False,
    unload_started_at: datetime | None = None,
    actual_dock_id: str | None = None,
) -> SchedulingShipment:
    return SchedulingShipment(
        shipment_id=shipment_id,
        facility_id="FAC-1",
        priority=priority,
        required_dock_type=dock_type,
        temperature_control_required=refrigerated,
        load_weight_kg=load_weight_kg,
        expected_unload_minutes=duration,
        eta_at=_at(eta_hour),
        eta_confidence=confidence,
        is_waiting=waiting,
        current_status="WAITING" if waiting else "IN_TRANSIT",
        unload_started_at=unload_started_at,
        actual_dock_id=actual_dock_id,
    )


def _slot(
    slot_id: str,
    *,
    start_hour: int,
    end_hour: int,
    dock_id: str = "D1",
    dock_type: str = "STANDARD",
    refrigerated: bool = False,
    max_weight: int = 20_000,
    availability: SlotAvailability = SlotAvailability.AVAILABLE,
    appointment_id: str | None = None,
    occupied_by: str | None = None,
) -> SchedulingSlot:
    return SchedulingSlot(
        slot_id=slot_id,
        facility_id="FAC-1",
        dock_id=dock_id,
        dock_code=dock_id,
        dock_type=dock_type,
        supports_refrigerated=refrigerated,
        max_vehicle_weight_kg=max_weight,
        starts_at=_at(start_hour),
        ends_at=_at(end_hour),
        availability=availability,
        appointment_id=appointment_id,
        occupied_by_shipment_id=occupied_by,
        appointment_status="CONFIRMED" if appointment_id is not None else None,
    )


def _snapshot(
    shipments: tuple[SchedulingShipment, ...],
    slots: tuple[SchedulingSlot, ...],
    *,
    appointments: tuple[ActiveAppointment, ...] = (),
    events: tuple[DockEvent, ...] = (),
    last_start: time | None = None,
) -> SchedulingSnapshot:
    return SchedulingSnapshot(
        facility=_facility(last_start=last_start),
        shipments=shipments,
        slots=slots,
        appointments=appointments,
        dock_events=events,
        observed_at=_at(9),
    )


def test_deterministic_evaluator_ranks_earliest_compatible_option_and_explains_rejections() -> None:
    shipment = _shipment()
    snapshot = _snapshot(
        (shipment,),
        (
            _slot("too-early", start_hour=9, end_hour=10),
            _slot("earliest", start_hour=10, end_hour=11),
            _slot("later", start_hour=11, end_hour=12),
            _slot("wrong-dock", start_hour=10, end_hour=11, dock_type="REEFER"),
        ),
    )

    result = DeterministicFeasibility().evaluate(snapshot, shipment.shipment_id)

    assert result.status is FeasibilityStatus.FEASIBLE
    assert [option.slot_id for option in result.options] == ["earliest", "later"]
    assessments = {assessment.slot_id: assessment.reasons for assessment in result.assessments}
    assert RejectionReason.ETA_AFTER_SLOT_START in assessments["too-early"]
    assert RejectionReason.INCOMPATIBLE_DOCK_TYPE in assessments["wrong-dock"]


def test_deterministic_evaluator_blocks_low_confidence_etas_and_non_matching_capacity() -> None:
    low_confidence = _shipment(confidence="LOW")
    review = DeterministicFeasibility().evaluate(
        _snapshot((low_confidence,), (_slot("candidate", start_hour=10, end_hour=11),)),
        low_confidence.shipment_id,
    )
    assert review.status is FeasibilityStatus.ETA_REVIEW_REQUIRED
    assert review.assessments[0].reasons == (RejectionReason.LOW_CONFIDENCE_ETA,)

    reefer = _shipment(refrigerated=True)
    capacity = DeterministicFeasibility().evaluate(
        _snapshot(
            (reefer,),
            (
                _slot("standard", start_hour=10, end_hour=11),
                _slot("short", start_hour=10, end_hour=10, dock_id="D2", refrigerated=True),
            ),
        ),
        reefer.shipment_id,
    )
    reasons = {assessment.slot_id: assessment.reasons for assessment in capacity.assessments}
    assert RejectionReason.REFRIGERATION_REQUIRED in reasons["standard"]
    assert RejectionReason.INSUFFICIENT_SLOT_DURATION in reasons["short"]


def test_after_hours_candidate_requires_operations_approval() -> None:
    shipment = _shipment(eta_hour=15)
    snapshot = _snapshot(
        (shipment,),
        (_slot("after-hours", start_hour=17, end_hour=18),),
        last_start=time(21),
    )
    result = DeterministicFeasibility().evaluate(snapshot, shipment.shipment_id)

    assert result.status is FeasibilityStatus.OPERATIONS_APPROVAL_REQUIRED
    assert result.options[0].requires_operations_approval is True


def test_in_dock_unload_blocks_following_slot_through_event_end() -> None:
    incoming = _shipment("incoming", eta_hour=10)
    unloading = _shipment(
        "unloading",
        eta_hour=8,
        duration=60,
        unload_started_at=_at(9, 30),
        actual_dock_id="D1",
    )
    unloading = replace(unloading, current_status="IN_DOCK")
    snapshot = _snapshot(
        (incoming, unloading),
        (_slot("next", start_hour=10, end_hour=11),),
        events=(
            DockEvent(
                dock_id="D1",
                event_type="CAPACITY_REDUCTION",
                starts_at=_at(9, 45),
                ends_at=_at(10, 45),
            ),
        ),
    )

    result = DeterministicFeasibility().evaluate(snapshot, incoming.shipment_id)

    assert result.status is FeasibilityStatus.NO_SOLUTION
    assert RejectionReason.IN_DOCK_UNLOAD in result.assessments[0].reasons


def test_cp_sat_proposes_only_a_lower_priority_appointment_move() -> None:
    target = _shipment("critical", priority=PriorityCode.CRITICAL, load_weight_kg=25_000)
    lower_priority = _shipment("low", priority=PriorityCode.LOW, eta_hour=9)
    occupied = _slot(
        "large-slot",
        start_hour=11,
        end_hour=12,
        max_weight=30_000,
        availability=SlotAvailability.OCCUPIED,
        appointment_id="APT-low",
        occupied_by="low",
    )
    alternative = _slot(
        "small-slot",
        start_hour=12,
        end_hour=13,
        dock_id="D2",
        max_weight=20_000,
    )
    snapshot = _snapshot(
        (target, lower_priority),
        (occupied, alternative),
        appointments=(
            ActiveAppointment(
                appointment_id="APT-low",
                shipment_id="low",
                slot_id="large-slot",
                status="CONFIRMED",
                priority=PriorityCode.LOW,
            ),
        ),
    )

    result = CpSatProposalOptimizer().propose(snapshot, target.shipment_id)

    assert result.status is OptimizationStatus.PROPOSAL_READY
    assert result.requires_warehouse_approval is True
    assert result.requires_affected_driver_approval is True
    assignments = {assignment.shipment_id: assignment for assignment in result.assignments}
    assert assignments["critical"].slot_id == "large-slot"
    assert assignments["critical"].is_reschedule is False
    assert assignments["low"].slot_id == "small-slot"
    assert assignments["low"].is_reschedule is True


def test_cp_sat_never_moves_equal_or_higher_priority_appointments() -> None:
    target = _shipment("target", priority=PriorityCode.HIGH, load_weight_kg=25_000)
    equal_priority = _shipment("existing", priority=PriorityCode.HIGH, eta_hour=9)
    snapshot = _snapshot(
        (target, equal_priority),
        (
            _slot(
                "only-slot",
                start_hour=11,
                end_hour=12,
                max_weight=30_000,
                availability=SlotAvailability.OCCUPIED,
                appointment_id="APT-existing",
                occupied_by="existing",
            ),
        ),
        appointments=(
            ActiveAppointment(
                appointment_id="APT-existing",
                shipment_id="existing",
                slot_id="only-slot",
                status="CONFIRMED",
                priority=PriorityCode.HIGH,
            ),
        ),
    )

    result = CpSatProposalOptimizer().propose(snapshot, target.shipment_id)

    assert result.status is OptimizationStatus.NO_SOLUTION


def test_cp_sat_batch_prioritizes_priority_then_yard_presence_then_eta() -> None:
    critical = _shipment("critical", priority=PriorityCode.CRITICAL)
    waiting_high = _shipment("waiting-high", priority=PriorityCode.HIGH, waiting=True)
    in_transit_high = _shipment("in-transit-high", priority=PriorityCode.HIGH, eta_hour=9)
    one_slot = _slot("shared", start_hour=11, end_hour=12)
    optimizer = CpSatProposalOptimizer()

    priority_result = optimizer.allocate_batch(
        _snapshot((critical, waiting_high), (one_slot,)),
        (critical.shipment_id, waiting_high.shipment_id),
    )
    assert priority_result.status is OptimizationStatus.PARTIAL
    assert priority_result.assignments[0].shipment_id == "critical"
    assert priority_result.unassigned_shipment_ids == ("waiting-high",)

    tie_result = optimizer.allocate_batch(
        _snapshot((waiting_high, in_transit_high), (one_slot,)),
        (waiting_high.shipment_id, in_transit_high.shipment_id),
    )
    assert tie_result.status is OptimizationStatus.PARTIAL
    assert tie_result.assignments[0].shipment_id == "waiting-high"
    assert tie_result.unassigned_shipment_ids == ("in-transit-high",)


def test_source_adapter_loads_all_scheduling_facts_from_the_seed_contract(tmp_path: Path) -> None:
    source_path = tmp_path / "setuhaul.db"
    source_path_sql = (BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text(
        encoding="utf-8"
    )
    with sqlite3.connect(source_path) as source:
        source.executescript(source_path_sql)

    connection, _ = connect_source_database(source_path)
    try:
        snapshot = load_snapshot_with_rules(
            SourceRepository(connection),
            "FAC-JAI-01",
            observed_at=_at(4),
        )
    finally:
        connection.close()

    assert snapshot.facility.last_new_start_time == time(21)
    assert snapshot.facility.heavy_dock_required_kg == 25_000
    assert snapshot.shipment("SHP1006").eta_confidence == "HIGH"
    assert any(slot.availability is SlotAvailability.BLOCKED for slot in snapshot.slots)
    assert any(event.event_type == "BREAKDOWN" for event in snapshot.dock_events)
