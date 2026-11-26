"""Local-only operational policy and exception-state transition guards."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Literal

from pydantic import model_validator

from dockit.schemas.contracts import ActorSubject, ContractModel, HoldStatus, SlotHold, UtcTimestamp

HOLD_TTL_SECONDS: Final[Literal[300]] = 300
REMINDER_AFTER_SECONDS: Final[Literal[150]] = 150


class HoldPolicyError(ValueError):
    """Raised when persisted hold timing conflicts with the confirmed policy."""


class HoldPolicy(ContractModel):
    """Confirmed option-delivery timer for local holds and chat reminders."""

    hold_ttl_seconds: Literal[300] = HOLD_TTL_SECONDS
    reminder_after_seconds: Literal[150] = REMINDER_AFTER_SECONDS

    def expires_at(self, options_provided_at_utc: UtcTimestamp) -> datetime:
        """Calculate the five-minute release deadline from option delivery."""

        return options_provided_at_utc + timedelta(seconds=self.hold_ttl_seconds)

    def reminder_at(self, options_provided_at_utc: UtcTimestamp) -> datetime:
        """Calculate when chat should prompt again if no response has arrived."""

        return options_provided_at_utc + timedelta(seconds=self.reminder_after_seconds)

    def validate_hold_timing(self, hold: SlotHold) -> None:
        """Ensure a persisted hold uses the confirmed option-delivery deadlines."""

        if hold.expires_at_utc != self.expires_at(hold.options_provided_at_utc):
            raise HoldPolicyError("hold expiry must be five minutes after options are provided")
        if (
            hold.reminder_sent_at_utc is not None
            and hold.reminder_sent_at_utc < self.reminder_at(hold.options_provided_at_utc)
        ):
            raise HoldPolicyError("hold reminder cannot be sent before the 2.5-minute deadline")

    def is_reminder_due(self, hold: SlotHold, *, at_utc: UtcTimestamp) -> bool:
        """Return whether one unanswered active hold needs its chat reminder."""

        self.validate_hold_timing(hold)
        return (
            hold.status is HoldStatus.ACTIVE
            and hold.reminder_sent_at_utc is None
            and self.reminder_at(hold.options_provided_at_utc) <= at_utc < hold.expires_at_utc
        )

    def is_expired(self, hold: SlotHold, *, at_utc: UtcTimestamp) -> bool:
        """Determine expiration without changing persisted hold state."""

        self.validate_hold_timing(hold)
        return hold.status is HoldStatus.ACTIVE and at_utc >= hold.expires_at_utc


class RevalidationPolicy(ContractModel):
    """Safety requirements before a local hold can ever become a reservation."""

    source_revalidation_required: Literal[True] = True
    active_hold_revalidation_required: Literal[True] = True
    authoritative_booking_required: Literal[True] = True


class WorkflowState(StrEnum):
    """Local exception lifecycle; confirmation remains gated by booking authority."""

    REPORTED = "REPORTED"
    TRIAGED = "TRIAGED"
    ALTERNATIVES_FOUND = "ALTERNATIVES_FOUND"
    PROPOSAL_PENDING_CONFIRMATION = "PROPOSAL_PENDING_CONFIRMATION"
    HELD = "HELD"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


_ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.REPORTED: frozenset(
        {WorkflowState.TRIAGED, WorkflowState.CANCELLED, WorkflowState.REJECTED}
    ),
    WorkflowState.TRIAGED: frozenset(
        {WorkflowState.ALTERNATIVES_FOUND, WorkflowState.CANCELLED, WorkflowState.REJECTED}
    ),
    WorkflowState.ALTERNATIVES_FOUND: frozenset(
        {
            WorkflowState.PROPOSAL_PENDING_CONFIRMATION,
            WorkflowState.CANCELLED,
            WorkflowState.REJECTED,
        }
    ),
    WorkflowState.PROPOSAL_PENDING_CONFIRMATION: frozenset(
        {WorkflowState.HELD, WorkflowState.CANCELLED, WorkflowState.REJECTED}
    ),
    WorkflowState.HELD: frozenset({WorkflowState.EXPIRED, WorkflowState.CANCELLED}),
    WorkflowState.EXPIRED: frozenset(),
    WorkflowState.CANCELLED: frozenset(),
    WorkflowState.REJECTED: frozenset(),
}


class InvalidStateTransition(ValueError):
    """Raised when a workflow skips or exits its approved local lifecycle."""


class OwnershipError(PermissionError):
    """Raised when an actor attempts to mutate another actor's local resource."""


def allowed_transitions(state: WorkflowState) -> frozenset[WorkflowState]:
    """Return the explicitly permitted next states for one lifecycle state."""

    return _ALLOWED_TRANSITIONS[state]


def assert_valid_transition(current: WorkflowState, target: WorkflowState) -> None:
    """Reject lifecycle jumps before a service performs a side effect."""

    if target not in allowed_transitions(current):
        raise InvalidStateTransition(f"cannot transition from {current} to {target}")


def assert_actor_ownership(actor_subject: ActorSubject, owner_subject: ActorSubject) -> None:
    """Apply the current exact-subject ownership rule until Cognito claims arrive."""

    if actor_subject != owner_subject:
        raise OwnershipError("actor does not own this resource")


class WorkflowTransition(ContractModel):
    """A validated request to advance a local exception lifecycle."""

    from_state: WorkflowState
    to_state: WorkflowState
    transitioned_at_utc: UtcTimestamp
    actor_subject: ActorSubject

    @model_validator(mode="after")
    def validate_transition(self) -> WorkflowTransition:
        assert_valid_transition(self.from_state, self.to_state)
        return self
