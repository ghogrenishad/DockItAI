"""Pure scheduling facts and results, independent of SQLite and HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo


class PriorityCode(StrEnum):
    """Confirmed allocation priority order."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SlotAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    BLOCKED = "BLOCKED"
    CLOSED = "CLOSED"


class RejectionReason(StrEnum):
    LOW_CONFIDENCE_ETA = "LOW_CONFIDENCE_ETA"
    SLOT_OCCUPIED = "SLOT_OCCUPIED"
    SLOT_NOT_OPEN = "SLOT_NOT_OPEN"
    ETA_AFTER_SLOT_START = "ETA_AFTER_SLOT_START"
    INCOMPATIBLE_DOCK_TYPE = "INCOMPATIBLE_DOCK_TYPE"
    REFRIGERATION_REQUIRED = "REFRIGERATION_REQUIRED"
    DOCK_WEIGHT_LIMIT = "DOCK_WEIGHT_LIMIT"
    HEAVY_DOCK_REQUIRED = "HEAVY_DOCK_REQUIRED"
    INSUFFICIENT_SLOT_DURATION = "INSUFFICIENT_SLOT_DURATION"
    IN_DOCK_UNLOAD = "IN_DOCK_UNLOAD"
    DOCK_EVENT_OVERLAP = "DOCK_EVENT_OVERLAP"
    OPERATIONS_APPROVAL_REQUIRED = "OPERATIONS_APPROVAL_REQUIRED"


class FeasibilityStatus(StrEnum):
    FEASIBLE = "FEASIBLE"
    OPERATIONS_APPROVAL_REQUIRED = "OPERATIONS_APPROVAL_REQUIRED"
    ETA_REVIEW_REQUIRED = "ETA_REVIEW_REQUIRED"
    NO_SOLUTION = "NO_SOLUTION"


class OptimizationStatus(StrEnum):
    DIRECT = "DIRECT"
    PROPOSAL_READY = "PROPOSAL_READY"
    PARTIAL = "PARTIAL"
    ETA_REVIEW_REQUIRED = "ETA_REVIEW_REQUIRED"
    NO_SOLUTION = "NO_SOLUTION"
    TIME_LIMIT = "TIME_LIMIT"


@dataclass(frozen=True)
class FacilitySchedule:
    facility_id: str
    timezone: ZoneInfo
    open_time: time
    close_time: time
    last_new_start_time: time | None
    heavy_dock_required_kg: int | None


@dataclass(frozen=True)
class SchedulingShipment:
    shipment_id: str
    facility_id: str
    priority: PriorityCode
    required_dock_type: str
    temperature_control_required: bool
    load_weight_kg: int
    expected_unload_minutes: int
    eta_at: datetime
    eta_confidence: str
    is_waiting: bool
    current_status: str
    unload_started_at: datetime | None
    actual_dock_id: str | None


@dataclass(frozen=True)
class SchedulingSlot:
    slot_id: str
    facility_id: str
    dock_id: str
    dock_code: str
    dock_type: str
    supports_refrigerated: bool
    max_vehicle_weight_kg: int
    starts_at: datetime
    ends_at: datetime
    availability: SlotAvailability
    appointment_id: str | None
    occupied_by_shipment_id: str | None
    appointment_status: str | None


@dataclass(frozen=True)
class DockEvent:
    dock_id: str
    event_type: str
    starts_at: datetime
    ends_at: datetime | None


@dataclass(frozen=True)
class ActiveAppointment:
    appointment_id: str
    shipment_id: str
    slot_id: str
    status: str
    priority: PriorityCode


@dataclass(frozen=True)
class SchedulingSnapshot:
    facility: FacilitySchedule
    shipments: tuple[SchedulingShipment, ...]
    slots: tuple[SchedulingSlot, ...]
    appointments: tuple[ActiveAppointment, ...]
    dock_events: tuple[DockEvent, ...]
    observed_at: datetime

    def shipment(self, shipment_id: str) -> SchedulingShipment:
        for shipment in self.shipments:
            if shipment.shipment_id == shipment_id:
                return shipment
        raise KeyError(f"unknown scheduling shipment: {shipment_id}")


@dataclass(frozen=True)
class SlotAssessment:
    slot_id: str
    reasons: tuple[RejectionReason, ...]
    requires_operations_approval: bool = False


@dataclass(frozen=True)
class FeasibleOption:
    shipment_id: str
    slot_id: str
    dock_code: str
    starts_at: datetime
    ends_at: datetime
    requires_operations_approval: bool
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class FeasibilityResult:
    status: FeasibilityStatus
    options: tuple[FeasibleOption, ...]
    assessments: tuple[SlotAssessment, ...]


@dataclass(frozen=True)
class SlotAssignment:
    shipment_id: str
    slot_id: str
    appointment_id: str | None
    is_reschedule: bool


@dataclass(frozen=True)
class OptimizationResult:
    status: OptimizationStatus
    assignments: tuple[SlotAssignment, ...]
    unassigned_shipment_ids: tuple[str, ...]
    assessments: tuple[SlotAssessment, ...]
    requires_warehouse_approval: bool
    requires_affected_driver_approval: bool


def priority_score(priority: PriorityCode) -> int:
    """Map the confirmed priority order to a stable numeric solver objective."""

    return {
        PriorityCode.LOW: 1,
        PriorityCode.NORMAL: 2,
        PriorityCode.HIGH: 3,
        PriorityCode.CRITICAL: 4,
    }[priority]
