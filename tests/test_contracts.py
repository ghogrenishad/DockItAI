"""Step 3 Pydantic contract and operational-policy tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from dockit.schemas.contracts import (
    AppointmentData,
    AppointmentStatus,
    AuditEvent,
    CandidateOption,
    ConfirmationRequest,
    DelayReport,
    ErrorResponse,
    FacilityTime,
    HoldStatus,
    IdempotencyScope,
    OptionExplanation,
    Page,
    SlotHold,
)
from dockit.schemas.policy import (
    HoldPolicy,
    HoldPolicyError,
    InvalidStateTransition,
    OwnershipError,
    RevalidationPolicy,
    WorkflowState,
    WorkflowTransition,
    allowed_transitions,
    assert_actor_ownership,
    assert_valid_transition,
)


def test_contracts_normalize_aware_timestamps_to_utc_and_preserve_facility_display() -> None:
    report = DelayReport(
        shipment_id="SHP1006",
        actor_subject="driver:DRV006",
        reported_at_utc="2026-08-04T09:35:00+05:30",
        declared_eta_at_utc="2026-08-04T11:20:00+05:30",
    )
    facility_time = FacilityTime(
        at_utc=report.declared_eta_at_utc,
        timezone_name="Asia/Kolkata",
    )

    assert report.reported_at_utc.isoformat() == "2026-08-04T04:05:00+00:00"
    assert report.declared_eta_at_utc.isoformat() == "2026-08-04T05:50:00+00:00"
    assert facility_time.as_local().isoformat() == "2026-08-04T11:20:00+05:30"


def test_contracts_reject_naive_timestamps_and_delay_reports_without_arrival_information() -> None:
    with pytest.raises(ValidationError, match="timezone offset"):
        DelayReport(
            shipment_id="SHP1006",
            actor_subject="driver:DRV006",
            reported_at_utc="2026-08-04T09:35:00",
            reported_delay_minutes=45,
        )

    with pytest.raises(ValidationError, match="declared_eta_at_utc or reported_delay_minutes"):
        DelayReport(
            shipment_id="SHP1006",
            actor_subject="driver:DRV006",
            reported_at_utc="2026-08-04T04:05:00Z",
        )


def test_candidate_options_require_a_valid_slot_window_and_explanation() -> None:
    with pytest.raises(ValidationError, match="slot_end_at_utc"):
        CandidateOption(
            option_id="option:001",
            shipment_id="SHP1006",
            facility_id="FAC-JAI-01",
            slot_id="SLOT-JAI-060",
            slot_start_at_utc="2026-08-04T07:30:00Z",
            slot_end_at_utc="2026-08-04T07:30:00Z",
            source_observed_at_utc="2026-08-04T04:05:00Z",
            explanations=(
                OptionExplanation(code="compatible_dock", message="Compatible standard dock."),
            ),
        )


def test_local_hold_expiration_and_finalization_invariants() -> None:
    hold = SlotHold(
        hold_id="hold:001",
        source_shipment_id="SHP1006",
        source_slot_id="SLOT-JAI-060",
        actor_subject="driver:DRV006",
        status=HoldStatus.ACTIVE,
        created_at_utc="2026-08-04T04:05:00Z",
        options_provided_at_utc="2026-08-04T04:05:00Z",
        expires_at_utc="2026-08-04T04:10:00Z",
    )
    policy = HoldPolicy()

    assert policy.hold_ttl_seconds == 300
    assert policy.reminder_after_seconds == 150
    assert policy.expires_at(hold.options_provided_at_utc) == hold.expires_at_utc
    assert policy.reminder_at(hold.options_provided_at_utc).isoformat() == (
        "2026-08-04T04:07:30+00:00"
    )
    assert policy.is_reminder_due(hold, at_utc=policy.reminder_at(hold.options_provided_at_utc))
    assert not policy.is_reminder_due(
        hold,
        at_utc=policy.reminder_at(hold.options_provided_at_utc) - timedelta(microseconds=1),
    )
    assert policy.is_expired(hold, at_utc=hold.expires_at_utc) is True
    assert policy.is_expired(
        hold,
        at_utc=hold.expires_at_utc - timedelta(microseconds=1),
    ) is False

    with pytest.raises(ValidationError, match="finalized hold requires"):
        SlotHold(
            hold_id="hold:002",
            source_shipment_id="SHP1006",
            source_slot_id="SLOT-JAI-061",
            actor_subject="driver:DRV006",
            status=HoldStatus.EXPIRED,
            created_at_utc="2026-08-04T04:05:00Z",
            options_provided_at_utc="2026-08-04T04:05:00Z",
            expires_at_utc="2026-08-04T04:10:00Z",
        )

    with pytest.raises(HoldPolicyError, match="five minutes"):
        policy.validate_hold_timing(
            SlotHold(
                hold_id="hold:003",
                source_shipment_id="SHP1006",
                source_slot_id="SLOT-JAI-062",
                actor_subject="driver:DRV006",
                status=HoldStatus.ACTIVE,
                created_at_utc="2026-08-04T04:05:00Z",
                options_provided_at_utc="2026-08-04T04:05:00Z",
                expires_at_utc="2026-08-04T04:09:59Z",
            )
        )

    reminded_hold = SlotHold(
        hold_id="hold:004",
        source_shipment_id="SHP1006",
        source_slot_id="SLOT-JAI-063",
        actor_subject="driver:DRV006",
        status=HoldStatus.ACTIVE,
        created_at_utc="2026-08-04T04:05:00Z",
        options_provided_at_utc="2026-08-04T04:05:00Z",
        expires_at_utc="2026-08-04T04:10:00Z",
        reminder_sent_at_utc="2026-08-04T04:07:30Z",
    )
    assert not policy.is_reminder_due(
        reminded_hold,
        at_utc=reminded_hold.reminder_sent_at_utc + timedelta(seconds=30),
    )


def test_confirmation_scope_binds_retries_to_the_requesting_actor() -> None:
    idempotency = IdempotencyScope(
        actor_subject="driver:DRV006",
        operation="confirm_hold",
        key="retry-key-001",
        request_fingerprint="a" * 64,
    )
    request = ConfirmationRequest(
        hold_id="hold:001",
        actor_subject="driver:DRV006",
        requested_at_utc="2026-08-04T04:06:00Z",
        idempotency=idempotency,
    )

    assert request.idempotency == idempotency
    with pytest.raises(ValidationError, match="must match idempotency actor"):
        ConfirmationRequest(
            hold_id="hold:001",
            actor_subject="driver:DRV999",
            requested_at_utc="2026-08-04T04:06:00Z",
            idempotency=idempotency,
        )


def test_revalidation_requirements_cannot_be_disabled_in_the_contract() -> None:
    assert RevalidationPolicy().model_dump() == {
        "source_revalidation_required": True,
        "active_hold_revalidation_required": True,
        "authoritative_booking_required": True,
    }
    with pytest.raises(ValidationError):
        RevalidationPolicy(source_revalidation_required=False)


def test_workflow_rejects_invalid_jumps_and_has_no_local_confirmation_state() -> None:
    assert allowed_transitions(WorkflowState.REPORTED) == frozenset(
        {WorkflowState.TRIAGED, WorkflowState.CANCELLED, WorkflowState.REJECTED}
    )
    assert "CONFIRMED" not in WorkflowState

    assert_valid_transition(WorkflowState.REPORTED, WorkflowState.TRIAGED)
    with pytest.raises(InvalidStateTransition, match="TRIAGED to HELD"):
        assert_valid_transition(WorkflowState.TRIAGED, WorkflowState.HELD)
    with pytest.raises(ValidationError, match="TRIAGED to HELD"):
        WorkflowTransition(
            from_state=WorkflowState.TRIAGED,
            to_state=WorkflowState.HELD,
            transitioned_at_utc="2026-08-04T04:06:00Z",
            actor_subject="driver:DRV006",
        )


def test_actor_ownership_and_openapi_contract_examples_are_explicit() -> None:
    assert_actor_ownership("driver:DRV006", "driver:DRV006")
    with pytest.raises(OwnershipError, match="does not own"):
        assert_actor_ownership("driver:DRV999", "driver:DRV006")

    assert DelayReport.model_json_schema()["examples"]
    assert ErrorResponse.model_json_schema()["examples"]
    audit_event = AuditEvent(
        audit_event_id="audit:001",
        occurred_at_utc="2026-08-04T04:06:00Z",
        actor_subject="driver:DRV006",
        event_type="hold_created",
        entity_type="slot_hold",
        entity_id="hold:001",
        payload={"hold_status": "ACTIVE"},
        state_hold_id="hold:001",
    )
    with pytest.raises(ValidationError, match="frozen_instance"):
        audit_event.event_type = "hold_released"  # type: ignore[misc]


def test_appointment_and_pagination_contracts_serialize_without_transport_code() -> None:
    appointment = AppointmentData(
        appointment_id="APT1006",
        shipment_id="SHP1006",
        slot_id="SLOT-JAI-045",
        status=AppointmentStatus.CONFIRMED,
        is_current=True,
        booked_at_utc="2026-08-04T10:55:00+05:30",
        confirmed_at_utc="2026-08-04T11:00:00+05:30",
    )
    page = Page[AppointmentData](items=(appointment,), next_cursor="cursor:next")

    assert page.model_dump(mode="json") == {
        "items": [
            {
                "appointment_id": "APT1006",
                "shipment_id": "SHP1006",
                "slot_id": "SLOT-JAI-045",
                "status": "CONFIRMED",
                "is_current": True,
                "booked_at_utc": "2026-08-04T05:25:00Z",
                "confirmed_at_utc": "2026-08-04T05:30:00Z",
                "cancelled_at_utc": None,
                "cancellation_reason": None,
            }
        ],
        "next_cursor": "cursor:next",
    }
