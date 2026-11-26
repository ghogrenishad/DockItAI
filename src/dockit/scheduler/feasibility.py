"""Transparent deterministic feasibility evaluation and stable option ranking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from dockit.scheduler.models import (
    FeasibilityResult,
    FeasibilityStatus,
    FeasibleOption,
    RejectionReason,
    SchedulingShipment,
    SchedulingSlot,
    SchedulingSnapshot,
    SlotAssessment,
    SlotAvailability,
)


@dataclass(frozen=True)
class DeterministicFeasibility:
    """Evaluate one shipment without mutating source or DockIt state."""

    def evaluate(
        self,
        snapshot: SchedulingSnapshot,
        shipment_id: str,
        *,
        reclaimable_appointment_ids: frozenset[str] = frozenset(),
    ) -> FeasibilityResult:
        shipment = snapshot.shipment(shipment_id)
        if shipment.eta_confidence != "HIGH":
            return FeasibilityResult(
                status=FeasibilityStatus.ETA_REVIEW_REQUIRED,
                options=(),
                assessments=tuple(
                    SlotAssessment(slot.slot_id, (RejectionReason.LOW_CONFIDENCE_ETA,))
                    for slot in snapshot.slots
                ),
            )

        assessments = tuple(
            self.assess_slot(
                snapshot,
                shipment,
                slot,
                reclaimable_appointment_ids=reclaimable_appointment_ids,
            )
            for slot in snapshot.slots
        )
        options = tuple(
            sorted(
                (
                    self._option(shipment, slot, assessment)
                    for slot, assessment in zip(snapshot.slots, assessments, strict=True)
                    if not assessment.reasons
                ),
                key=lambda option: (
                    option.requires_operations_approval,
                    option.starts_at,
                    option.ends_at,
                    option.dock_code,
                    option.slot_id,
                ),
            )
        )
        automatic_options = tuple(
            option for option in options if not option.requires_operations_approval
        )
        approval_options = tuple(
            option for option in options if option.requires_operations_approval
        )
        if automatic_options:
            return FeasibilityResult(FeasibilityStatus.FEASIBLE, automatic_options, assessments)
        if approval_options:
            return FeasibilityResult(
                FeasibilityStatus.OPERATIONS_APPROVAL_REQUIRED,
                approval_options,
                assessments,
            )
        return FeasibilityResult(FeasibilityStatus.NO_SOLUTION, (), assessments)

    def assess_slot(
        self,
        snapshot: SchedulingSnapshot,
        shipment: SchedulingShipment,
        slot: SchedulingSlot,
        *,
        reclaimable_appointment_ids: frozenset[str] = frozenset(),
    ) -> SlotAssessment:
        """List every hard rejection reason for one shipment/slot pairing."""

        reasons: list[RejectionReason] = []
        if slot.availability is SlotAvailability.OCCUPIED:
            if slot.appointment_id not in reclaimable_appointment_ids:
                reasons.append(RejectionReason.SLOT_OCCUPIED)
        elif slot.availability is not SlotAvailability.AVAILABLE:
            reasons.append(RejectionReason.SLOT_NOT_OPEN)
        if slot.starts_at < shipment.eta_at:
            reasons.append(RejectionReason.ETA_AFTER_SLOT_START)
        if not self._dock_type_matches(shipment, slot):
            reasons.append(RejectionReason.INCOMPATIBLE_DOCK_TYPE)
        if shipment.temperature_control_required and not slot.supports_refrigerated:
            reasons.append(RejectionReason.REFRIGERATION_REQUIRED)
        if shipment.load_weight_kg > slot.max_vehicle_weight_kg:
            reasons.append(RejectionReason.DOCK_WEIGHT_LIMIT)
        if (
            snapshot.facility.heavy_dock_required_kg is not None
            and shipment.load_weight_kg > snapshot.facility.heavy_dock_required_kg
            and slot.dock_type != "HEAVY"
        ):
            reasons.append(RejectionReason.HEAVY_DOCK_REQUIRED)
        if slot.ends_at - slot.starts_at < timedelta(minutes=shipment.expected_unload_minutes):
            reasons.append(RejectionReason.INSUFFICIENT_SLOT_DURATION)
        if self._overlaps_in_dock_unload(snapshot, slot):
            reasons.append(RejectionReason.IN_DOCK_UNLOAD)
        if self._overlaps_blocking_event(snapshot, slot):
            reasons.append(RejectionReason.DOCK_EVENT_OVERLAP)

        requires_approval = self._requires_operations_approval(snapshot, slot)
        if requires_approval:
            reasons.append(RejectionReason.OPERATIONS_APPROVAL_REQUIRED)
        hard_reasons = tuple(
            reason
            for reason in reasons
            if reason is not RejectionReason.OPERATIONS_APPROVAL_REQUIRED
        )
        return SlotAssessment(slot.slot_id, hard_reasons, requires_approval and not hard_reasons)

    @staticmethod
    def _dock_type_matches(shipment: SchedulingShipment, slot: SchedulingSlot) -> bool:
        return shipment.required_dock_type == "ANY" or shipment.required_dock_type == slot.dock_type

    @staticmethod
    def _overlaps_in_dock_unload(snapshot: SchedulingSnapshot, slot: SchedulingSlot) -> bool:
        for active in snapshot.shipments:
            if active.current_status != "IN_DOCK" or active.actual_dock_id != slot.dock_id:
                continue
            if active.unload_started_at is None:
                return True
            release_at = active.unload_started_at + timedelta(
                minutes=active.expected_unload_minutes
            )
            for event in snapshot.dock_events:
                if event.dock_id != slot.dock_id or event.event_type == "REOPENED":
                    continue
                if event.ends_at is None and event.starts_at < release_at:
                    return True
                if event.ends_at is not None and event.starts_at < release_at:
                    release_at = max(release_at, event.ends_at)
            if slot.starts_at < release_at:
                return True
        return False

    @staticmethod
    def _overlaps_blocking_event(snapshot: SchedulingSnapshot, slot: SchedulingSlot) -> bool:
        for event in snapshot.dock_events:
            if event.dock_id != slot.dock_id or event.event_type == "REOPENED":
                continue
            if event.ends_at is None:
                if slot.ends_at > event.starts_at:
                    return True
            elif event.starts_at < slot.ends_at and event.ends_at > slot.starts_at:
                return True
        return False

    @staticmethod
    def _requires_operations_approval(snapshot: SchedulingSnapshot, slot: SchedulingSlot) -> bool:
        start = slot.starts_at.astimezone(snapshot.facility.timezone).time()
        end = slot.ends_at.astimezone(snapshot.facility.timezone).time()
        if start < snapshot.facility.open_time or end > snapshot.facility.close_time:
            return True
        return (
            snapshot.facility.last_new_start_time is not None
            and start > snapshot.facility.last_new_start_time
        )

    @staticmethod
    def _option(
        shipment: SchedulingShipment,
        slot: SchedulingSlot,
        assessment: SlotAssessment,
    ) -> FeasibleOption:
        explanation = (
            "Requires operations approval for the operating-hours exception."
            if assessment.requires_operations_approval
            else "Meets ETA, dock, capacity, duration, and event constraints."
        )
        return FeasibleOption(
            shipment_id=shipment.shipment_id,
            slot_id=slot.slot_id,
            dock_code=slot.dock_code,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            requires_operations_approval=assessment.requires_operations_approval,
            explanations=(explanation,),
        )
