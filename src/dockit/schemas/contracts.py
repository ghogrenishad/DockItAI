"""Pydantic contracts shared across future API, workflow, and scheduling layers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"

type EntityId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN),
]
type ActorSubject = EntityId
type AppointmentId = EntityId
type AuditEventId = EntityId
type ConversationReferenceId = EntityId
type DriverId = EntityId
type FacilityId = EntityId
type HoldId = EntityId
type OptionId = EntityId
type ShipmentId = EntityId
type SlotId = EntityId
type Cursor = Annotated[str, StringConstraints(min_length=1, max_length=512)]
type OperationName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
type IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=255)]


def _normalise_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value.astimezone(UTC)


type UtcTimestamp = Annotated[datetime, AfterValidator(_normalise_utc)]


class ContractModel(BaseModel):
    """Base model that rejects fields unknown to the public contract."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FacilityTime(ContractModel):
    """An instant stored in UTC with the IANA timezone used for local presentation."""

    at_utc: UtcTimestamp
    timezone_name: str = Field(min_length=1, max_length=64)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timezone_name must be a valid IANA timezone") from error
        return value

    def as_local(self) -> datetime:
        """Return the stored instant in its explicit facility timezone."""

        return self.at_utc.astimezone(ZoneInfo(self.timezone_name))


class AppointmentStatus(StrEnum):
    """Appointment statuses supplied by the SetuHaul source contract."""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    REJECTED = "REJECTED"


class AppointmentData(ContractModel):
    """Read-model shape for a source appointment; not a source mutation command."""

    appointment_id: AppointmentId
    shipment_id: ShipmentId
    slot_id: SlotId
    status: AppointmentStatus
    is_current: bool
    booked_at_utc: UtcTimestamp
    confirmed_at_utc: UtcTimestamp | None = None
    cancelled_at_utc: UtcTimestamp | None = None
    cancellation_reason: str | None = Field(default=None, max_length=500)


class DelayReport(ContractModel):
    """A driver or operations delay declaration awaiting workflow processing."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "shipment_id": "SHP1006",
                    "actor_subject": "driver:DRV006",
                    "reported_at_utc": "2026-08-04T04:05:00Z",
                    "declared_eta_at_utc": "2026-08-04T05:50:00Z",
                    "reason_code": "TRAFFIC",
                }
            ]
        },
    )

    shipment_id: ShipmentId
    actor_subject: ActorSubject
    reported_at_utc: UtcTimestamp
    declared_eta_at_utc: UtcTimestamp | None = None
    reported_delay_minutes: int | None = Field(default=None, gt=0)
    reason_code: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_arrival_information(self) -> DelayReport:
        if self.declared_eta_at_utc is None and self.reported_delay_minutes is None:
            raise ValueError(
                "a delay report requires declared_eta_at_utc or reported_delay_minutes"
            )
        return self


class OptionExplanation(ContractModel):
    """A deterministic explanation for presenting a candidate option."""

    code: OperationName
    message: str = Field(min_length=1, max_length=500)


class CandidateOption(ContractModel):
    """A non-binding slot option produced by a future feasibility evaluator."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "option_id": "option:001",
                    "shipment_id": "SHP1006",
                    "facility_id": "FAC-JAI-01",
                    "slot_id": "SLOT-JAI-060",
                    "slot_start_at_utc": "2026-08-04T07:30:00Z",
                    "slot_end_at_utc": "2026-08-04T08:30:00Z",
                    "source_observed_at_utc": "2026-08-04T04:05:00Z",
                    "explanations": [
                        {"code": "compatible_dock", "message": "Compatible standard dock."}
                    ],
                }
            ]
        },
    )

    option_id: OptionId
    shipment_id: ShipmentId
    facility_id: FacilityId
    slot_id: SlotId
    slot_start_at_utc: UtcTimestamp
    slot_end_at_utc: UtcTimestamp
    source_observed_at_utc: UtcTimestamp
    explanations: tuple[OptionExplanation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_valid_slot_window(self) -> CandidateOption:
        if self.slot_end_at_utc <= self.slot_start_at_utc:
            raise ValueError("slot_end_at_utc must be after slot_start_at_utc")
        return self


class HoldStatus(StrEnum):
    """Statuses persisted by the local `slot_holds` state table."""

    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class SlotHold(ContractModel):
    """A local, non-authoritative hold that must be revalidated before confirmation."""

    hold_id: HoldId
    source_shipment_id: ShipmentId
    source_slot_id: SlotId
    actor_subject: ActorSubject
    conversation_reference_id: ConversationReferenceId | None = None
    status: HoldStatus
    created_at_utc: UtcTimestamp
    options_provided_at_utc: UtcTimestamp
    expires_at_utc: UtcTimestamp
    reminder_sent_at_utc: UtcTimestamp | None = None
    finalized_at_utc: UtcTimestamp | None = None

    @model_validator(mode="after")
    def validate_lifecycle_timestamps(self) -> SlotHold:
        if self.expires_at_utc <= self.options_provided_at_utc:
            raise ValueError("expires_at_utc must be after options_provided_at_utc")
        if (
            self.reminder_sent_at_utc is not None
            and self.reminder_sent_at_utc < self.options_provided_at_utc
        ):
            raise ValueError("reminder_sent_at_utc cannot be before options_provided_at_utc")
        if (
            self.reminder_sent_at_utc is not None
            and self.reminder_sent_at_utc >= self.expires_at_utc
        ):
            raise ValueError("reminder_sent_at_utc must be before expires_at_utc")
        if self.status is HoldStatus.ACTIVE and self.finalized_at_utc is not None:
            raise ValueError("an active hold cannot have finalized_at_utc")
        if self.status is not HoldStatus.ACTIVE and self.finalized_at_utc is None:
            raise ValueError("a finalized hold requires finalized_at_utc")
        if self.finalized_at_utc is not None and self.finalized_at_utc < self.created_at_utc:
            raise ValueError("finalized_at_utc cannot be before created_at_utc")
        return self


class IdempotencyScope(ContractModel):
    """A request identity scoped to one actor and one mutation operation."""

    actor_subject: ActorSubject
    operation: OperationName
    key: IdempotencyKey
    request_fingerprint: str = Field(min_length=1, max_length=128)


class ConfirmationRequest(ContractModel):
    """An explicit user request, not evidence of an authoritative reservation."""

    hold_id: HoldId
    actor_subject: ActorSubject
    requested_at_utc: UtcTimestamp
    idempotency: IdempotencyScope

    @model_validator(mode="after")
    def require_request_owner_to_match_scope(self) -> ConfirmationRequest:
        if self.actor_subject != self.idempotency.actor_subject:
            raise ValueError("confirmation actor must match idempotency actor")
        return self


type AuditPayloadValue = str | int | float | bool | None


class AuditEvent(ContractModel):
    """Immutable application audit shape; append, never update or delete."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    audit_event_id: AuditEventId
    occurred_at_utc: UtcTimestamp
    actor_subject: ActorSubject
    event_type: OperationName
    entity_type: OperationName
    entity_id: EntityId
    correlation_id: UUID | None = None
    payload: dict[str, AuditPayloadValue]
    state_hold_id: HoldId | None = None


class ErrorCode(StrEnum):
    """Stable, public error codes for future HTTP and tool adapters."""

    VALIDATION_ERROR = "validation_error"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PRECONDITION_FAILED = "precondition_failed"


class ErrorResponse(ContractModel):
    """Versionable error payload shape independent of transport implementation."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "code": "invalid_state_transition",
                    "message": "Cannot move from TRIAGED to HELD.",
                    "correlation_id": "123e4567-e89b-12d3-a456-426614174000",
                }
            ]
        },
    )

    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    correlation_id: UUID | None = None
    details: dict[str, AuditPayloadValue] | None = None


class Page[PageItem](ContractModel):
    """Cursor pagination envelope for future read endpoints."""

    items: tuple[PageItem, ...] = Field(default_factory=tuple)
    next_cursor: Cursor | None = None
