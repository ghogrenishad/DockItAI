"""Authenticated operational and conversation routes."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from dockit.agent.orchestration import (
    ConversationAction,
    ConversationIntent,
    ConversationOrchestrator,
    ConversationState,
    TypedWorkflowTool,
)
from dockit.api.dependencies import (
    get_booking_workflow,
    get_operational_workflow,
    get_source_repository,
    require_driver_principal,
)
from dockit.db.source_repository import SourceRecord, SourceRepository
from dockit.scheduler.feasibility import DeterministicFeasibility
from dockit.scheduler.source_adapter import load_snapshot_with_rules
from dockit.schemas.contracts import (
    BookingResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    DelayReportRequest,
    DelayReportResponse,
    DockOption,
    ErrorResponse,
    HoldCancellationResponse,
    HoldCreateRequest,
    HoldResponse,
    HoldStatus,
    OptionExplanation,
    OptionsResponse,
    Page,
    ProposalCreateRequest,
    ProposalResponse,
    ShipmentSnapshotResponse,
)
from dockit.services.booking import BookingConflict, BookingWorkflow, HoldExpired
from dockit.services.security import Principal
from dockit.services.workflows import OperationalWorkflow, WorkflowConflict

router = APIRouter(
    tags=["operations"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        428: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)

PrincipalDependency = Annotated[Principal, Depends(require_driver_principal)]
SourceDependency = Annotated[SourceRepository, Depends(get_source_repository)]
OperationalDependency = Annotated[OperationalWorkflow, Depends(get_operational_workflow)]
BookingDependency = Annotated[BookingWorkflow, Depends(get_booking_workflow)]
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def _owned_snapshot(
    repository: SourceRepository,
    principal: Principal,
    shipment_id: str,
) -> SourceRecord:
    """Resolve a shipment only through the authenticated driver's read boundary."""

    principal.require_group("drivers")
    row = repository.get_driver_shipment_snapshot(principal.driver_id or "", shipment_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shipment was not found")
    try:
        principal.require_driver_ownership(str(row["driver_id"]))
    except (KeyError, TypeError) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shipment was not found") from error
    return row


def _snapshot_response(row: SourceRecord) -> ShipmentSnapshotResponse:
    return ShipmentSnapshotResponse.model_validate(
        {
            "shipment_id": row["shipment_id"],
            "driver_id": row["driver_id"],
            "destination_facility_id": row["destination_facility_id"],
            "priority_code": row["priority_code"],
            "required_dock_type": row["required_dock_type"],
            "current_status": row["current_status"],
            "effective_eta_at_utc": row["effective_eta_ts"],
            "appointment_id": row["appointment_id"],
            "slot_id": row["slot_id"],
            "slot_start_at_utc": row["slot_start_ts"],
            "slot_end_at_utc": row["slot_end_ts"],
            "planned_dock_code": row["planned_dock_code"],
            "gate_in_at_utc": row["gate_in_ts"],
            "queue_state": row["queue_state"],
            "queue_position": row["queue_position"],
        }
    )


def _cursor_start(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = int(cursor)
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cursor is invalid") from error
    if value < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cursor is invalid")
    return value


def _timestamp_now() -> datetime:
    return datetime.now(UTC)


def _slot_is_in_facility(
    repository: SourceRepository,
    facility_id: str,
    slot_id: str,
) -> None:
    try:
        slots = repository.list_scheduling_slots(facility_id)
    except (KeyError, sqlite3.Error) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "facility was not found") from error
    if not any(row.get("slot_id") == slot_id for row in slots):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "slot was not found for this shipment")


def _workflow_conflict(error: WorkflowConflict) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.get("/shipments", response_model=Page[ShipmentSnapshotResponse])
async def list_shipments(
    principal: PrincipalDependency,
    repository: SourceDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[ShipmentSnapshotResponse]:
    """List only the authenticated driver's assigned shipment snapshots."""

    principal.require_group("drivers")
    rows = repository.list_driver_shipment_summaries(principal.driver_id or "")
    start = _cursor_start(cursor)
    page_rows = rows[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(rows) else None
    return Page[ShipmentSnapshotResponse](
        items=tuple(_snapshot_response(row) for row in page_rows),
        next_cursor=next_cursor,
    )


@router.get("/shipments/{shipment_id}", response_model=ShipmentSnapshotResponse)
async def get_shipment(
    shipment_id: str,
    principal: PrincipalDependency,
    repository: SourceDependency,
) -> ShipmentSnapshotResponse:
    """Return one driver-owned operational snapshot."""

    return _snapshot_response(_owned_snapshot(repository, principal, shipment_id))


def _get_options_response(
    shipment_id: str,
    principal: Principal,
    repository: SourceRepository,
) -> OptionsResponse:
    """Evaluate deterministic feasible options without creating a reservation."""

    row = _owned_snapshot(repository, principal, shipment_id)
    facility_id = str(row["destination_facility_id"])
    observed_at = _timestamp_now()
    try:
        snapshot = load_snapshot_with_rules(repository, facility_id, observed_at=observed_at)
        result = DeterministicFeasibility().evaluate(snapshot, shipment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "shipment was not found") from error
    except (ValueError, sqlite3.Error) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "scheduling data is unavailable",
        ) from error

    options = tuple(
        DockOption(
            option_id=f"option:{option.slot_id}",
            shipment_id=option.shipment_id,
            facility_id=facility_id,
            slot_id=option.slot_id,
            dock_code=option.dock_code,
            slot_start_at_utc=option.starts_at,
            slot_end_at_utc=option.ends_at,
            requires_operations_approval=option.requires_operations_approval,
            explanations=(
                OptionExplanation(
                    code=(
                        "operations_approval_required"
                        if option.requires_operations_approval
                        else "feasible"
                    ),
                    message=option.explanations[0],
                ),
            ),
        )
        for option in result.options
    )
    rejection_reasons = {
        assessment.slot_id: tuple(reason.value for reason in assessment.reasons)
        + (("OPERATIONS_APPROVAL_REQUIRED",) if assessment.requires_operations_approval else ())
        for assessment in result.assessments
        if assessment.reasons or assessment.requires_operations_approval
    }
    return OptionsResponse(
        shipment_id=shipment_id,
        facility_id=facility_id,
        observed_at_utc=observed_at,
        status=result.status.value,
        options=options,
        rejection_reasons=rejection_reasons,
    )


@router.get("/shipments/{shipment_id}/options", response_model=OptionsResponse)
async def get_options(
    shipment_id: str,
    principal: PrincipalDependency,
    repository: SourceDependency,
) -> OptionsResponse:
    """Evaluate deterministic feasible options without creating a reservation."""

    return _get_options_response(shipment_id, principal, repository)


@router.post("/shipments/{shipment_id}/delays", response_model=DelayReportResponse)
async def report_delay(
    shipment_id: str,
    request: DelayReportRequest,
    principal: PrincipalDependency,
    repository: SourceDependency,
    workflow: OperationalDependency,
) -> DelayReportResponse:
    """Record a driver delay through the transactional workflow service."""

    row = _owned_snapshot(repository, principal, shipment_id)
    declared_eta = request.declared_eta_at_utc
    if declared_eta is None:
        current_eta = row["effective_eta_ts"]
        if not isinstance(current_eta, str):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "declared_eta_at_utc is required when the shipment has no current ETA",
            )
        try:
            declared_eta = datetime.fromisoformat(current_eta).astimezone(UTC) + timedelta(
                minutes=request.reported_delay_minutes or 0
            )
        except ValueError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "shipment ETA is invalid",
            ) from error
    try:
        update_id = workflow.report_delay(
            shipment_id=shipment_id,
            actor_subject=principal.subject,
            declared_eta=declared_eta,
            reported_at=_timestamp_now(),
            reason_code=request.reason_code,
        )
    except WorkflowConflict as error:
        raise _workflow_conflict(error) from error
    return DelayReportResponse(
        update_id=update_id,
        shipment_id=shipment_id,
        declared_eta_at_utc=declared_eta,
    )


@router.post("/shipments/{shipment_id}/proposals", response_model=ProposalResponse)
async def create_proposal(
    shipment_id: str,
    request: ProposalCreateRequest,
    principal: PrincipalDependency,
    repository: SourceDependency,
    workflow: OperationalDependency,
) -> ProposalResponse:
    """Persist a non-booking proposal for a driver-owned shipment."""

    row = _owned_snapshot(repository, principal, shipment_id)
    _slot_is_in_facility(repository, str(row["destination_facility_id"]), request.slot_id)
    try:
        proposal_id = workflow.create_proposal(
            shipment_id=shipment_id,
            slot_id=request.slot_id,
            actor_subject=principal.subject,
            created_at=_timestamp_now(),
            requires_warehouse_approval=False,
            requires_affected_driver_approval=False,
        )
    except WorkflowConflict as error:
        raise _workflow_conflict(error) from error
    return ProposalResponse(
        proposal_id=proposal_id,
        shipment_id=shipment_id,
        slot_id=request.slot_id,
        status="PENDING_DRIVER",
        requires_warehouse_approval=False,
        requires_affected_driver_approval=False,
    )


@router.post("/shipments/{shipment_id}/holds", response_model=HoldResponse)
async def create_hold(
    shipment_id: str,
    request: HoldCreateRequest,
    principal: PrincipalDependency,
    repository: SourceDependency,
    workflow: OperationalDependency,
) -> HoldResponse:
    """Create a five-minute local hold after checking the driver-owned facility."""

    row = _owned_snapshot(repository, principal, shipment_id)
    _slot_is_in_facility(repository, str(row["destination_facility_id"]), request.slot_id)
    now = _timestamp_now()
    if request.options_observed_at_utc > now + timedelta(seconds=5):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "options_observed_at_utc cannot be in the future",
        )
    if now - request.options_observed_at_utc > timedelta(minutes=5):
        raise HTTPException(status.HTTP_409_CONFLICT, "the selected options have expired")
    created_at = request.options_observed_at_utc
    try:
        result = workflow.create_hold(
            shipment_id=shipment_id,
            slot_id=request.slot_id,
            actor_subject=principal.subject,
            options_provided_at=created_at,
        )
    except WorkflowConflict as error:
        raise _workflow_conflict(error) from error
    return HoldResponse(
        hold_id=result.hold_id,
        shipment_id=shipment_id,
        slot_id=request.slot_id,
        status=HoldStatus.ACTIVE,
        created_at_utc=created_at,
        expires_at_utc=datetime.fromisoformat(result.expires_at_utc),
    )


@router.post("/holds/{hold_id}/confirm", response_model=BookingResponse)
async def confirm_hold(
    hold_id: str,
    principal: PrincipalDependency,
    booking: BookingDependency,
    idempotency_key: IdempotencyHeader = None,
) -> BookingResponse:
    """Confirm a hold using the booking service's final atomic revalidation."""

    if not idempotency_key:
        raise HTTPException(
            status.HTTP_428_PRECONDITION_REQUIRED,
            "Idempotency-Key header required",
        )
    fingerprint = hashlib.sha256(f"confirm_hold:{hold_id}".encode()).hexdigest()
    try:
        result = booking.confirm_held_option(
            hold_id=hold_id,
            actor_subject=principal.subject,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            confirmed_at=_timestamp_now(),
        )
    except HoldExpired as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except BookingConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return BookingResponse(
        appointment_id=result.appointment_id,
        slot_id=result.slot_id,
        idempotent_replay=result.idempotent_replay,
    )


@router.delete("/holds/{hold_id}", response_model=HoldCancellationResponse)
async def cancel_hold(
    hold_id: str,
    principal: PrincipalDependency,
    workflow: OperationalDependency,
) -> HoldCancellationResponse:
    """Release an active local hold without creating a reservation."""

    try:
        workflow.release_hold(
            hold_id=hold_id,
            actor_subject=principal.subject,
            released_at=_timestamp_now(),
        )
    except WorkflowConflict as error:
        raise _workflow_conflict(error) from error
    return HoldCancellationResponse(hold_id=hold_id)


ConversationHandler = Callable[
    [Principal, ConversationIntent, ConversationState], Mapping[str, object]
]


class _ConversationTool:
    """Adapt one server-owned operation to the Step 7 typed tool protocol."""

    def __init__(self, handler: ConversationHandler) -> None:
        self._handler = handler

    def execute(
        self,
        *,
        principal: Principal,
        intent: ConversationIntent,
        state: ConversationState,
    ) -> Mapping[str, object]:
        try:
            return self._handler(principal, intent, state)
        except (BookingConflict, HoldExpired, HTTPException, WorkflowConflict):
            return {"status": "rejected"}


def _conversation_tools(
    request: Request,
    repository: SourceRepository,
    operational: OperationalWorkflow | None,
    booking: BookingWorkflow | None,
) -> Mapping[str, TypedWorkflowTool]:
    """Build an allow-list of typed tools for one authenticated chat request."""

    def report(
        principal: Principal,
        intent: ConversationIntent,
        _state: ConversationState,
    ) -> Mapping[str, object]:
        if operational is None or intent.shipment_id is None:
            raise WorkflowConflict("shipment and operational workflow are required")
        row = _owned_snapshot(repository, principal, intent.shipment_id)
        declared_eta = intent.declared_eta_at_utc
        if declared_eta is None and intent.delay_minutes is not None:
            current_eta = row["effective_eta_ts"]
            if not isinstance(current_eta, str):
                raise WorkflowConflict("a declared ETA is required")
            try:
                declared_eta = datetime.fromisoformat(current_eta).astimezone(UTC) + timedelta(
                    minutes=int(intent.delay_minutes)
                )
            except ValueError as error:
                raise WorkflowConflict("shipment ETA is invalid") from error
        if declared_eta is None:
            raise WorkflowConflict("a declared ETA is required")
        update_id = operational.report_delay(
            shipment_id=intent.shipment_id,
            actor_subject=principal.subject,
            declared_eta=declared_eta,
            reported_at=_timestamp_now(),
            reason_code=intent.reason_code,
        )
        return {"status": "delay_reported", "update_id": update_id}

    def find_options(
        principal: Principal,
        intent: ConversationIntent,
        _state: ConversationState,
    ) -> Mapping[str, object]:
        if intent.shipment_id is None:
            raise WorkflowConflict("shipment ID is required")
        response = _get_options_response(intent.shipment_id, principal, repository)
        return {
            "status": "options_ready",
            "options": response.model_dump(mode="json"),
            "options_observed_at_utc": response.observed_at_utc.isoformat(),
        }

    def create_hold_from_chat(
        principal: Principal,
        intent: ConversationIntent,
        state: ConversationState,
    ) -> Mapping[str, object]:
        if operational is None or intent.shipment_id is None or intent.slot_id is None:
            raise WorkflowConflict("shipment, slot, and operational workflow are required")
        if state.options_observed_at_utc is None:
            raise WorkflowConflict("options must be requested before a hold")
        now = _timestamp_now()
        if state.options_observed_at_utc > now + timedelta(seconds=5):
            raise WorkflowConflict("options timestamp is invalid")
        if now - state.options_observed_at_utc > timedelta(minutes=5):
            raise WorkflowConflict("the selected options have expired")
        row = _owned_snapshot(repository, principal, intent.shipment_id)
        _slot_is_in_facility(
            repository,
            str(row["destination_facility_id"]),
            intent.slot_id,
        )
        result = operational.create_hold(
            shipment_id=intent.shipment_id,
            slot_id=intent.slot_id,
            actor_subject=principal.subject,
            options_provided_at=state.options_observed_at_utc,
        )
        return {
            "status": "hold_created",
            "requires_confirmation": True,
            "hold_id": result.hold_id,
            "expires_at_utc": result.expires_at_utc,
        }

    def confirm(
        principal: Principal,
        intent: ConversationIntent,
        state: ConversationState,
    ) -> Mapping[str, object]:
        if booking is None or intent.hold_id is None:
            raise WorkflowConflict("booking workflow is unavailable")
        key = f"chat:{state.thread_id}:{intent.hold_id}"
        fingerprint = hashlib.sha256(key.encode()).hexdigest()
        result = booking.confirm_held_option(
            hold_id=intent.hold_id,
            actor_subject=principal.subject,
            idempotency_key=key,
            request_fingerprint=fingerprint,
            confirmed_at=_timestamp_now(),
        )
        return {
            "status": "confirmed",
            "confirmed": True,
            "appointment_id": result.appointment_id,
            "slot_id": result.slot_id,
        }

    def release(
        principal: Principal,
        intent: ConversationIntent,
        _state: ConversationState,
    ) -> Mapping[str, object]:
        if operational is None or intent.hold_id is None:
            raise WorkflowConflict("operational workflow is unavailable")
        operational.release_hold(
            hold_id=intent.hold_id,
            actor_subject=principal.subject,
            released_at=_timestamp_now(),
        )
        return {"status": "released", "hold_id": intent.hold_id}

    def status_tool(
        principal: Principal,
        intent: ConversationIntent,
        _state: ConversationState,
    ) -> Mapping[str, object]:
        if intent.shipment_id is None:
            raise WorkflowConflict("shipment ID is required")
        snapshot = _snapshot_response(_owned_snapshot(repository, principal, intent.shipment_id))
        return {"status": "available", "shipment": snapshot.model_dump(mode="json")}

    del request
    return {
        ConversationAction.REPORT_DELAY: _ConversationTool(report),
        ConversationAction.FIND_OPTIONS: _ConversationTool(find_options),
        ConversationAction.CREATE_HOLD: _ConversationTool(create_hold_from_chat),
        ConversationAction.CONFIRM_HOLD: _ConversationTool(confirm),
        ConversationAction.RELEASE_HOLD: _ConversationTool(release),
        ConversationAction.STATUS: _ConversationTool(status_tool),
    }


@router.post("/chat/{thread_id}/messages", response_model=ChatMessageResponse)
async def chat_message(
    thread_id: str,
    request: ChatMessageRequest,
    principal: PrincipalDependency,
    source_repository: SourceDependency,
    http_request: Request,
) -> ChatMessageResponse:
    """Send one message through the constrained conversation boundary."""

    orchestrator = getattr(http_request.app.state, "conversation_orchestrator", None)
    if not isinstance(orchestrator, ConversationOrchestrator):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "conversation service is unavailable",
        )
    operational = getattr(http_request.app.state, "operational_workflow", None)
    booking = getattr(http_request.app.state, "booking_workflow", None)
    response = orchestrator.handle(
        principal=principal,
        thread_id=thread_id,
        message=request.message,
        tools=_conversation_tools(
            http_request,
            source_repository,
            operational if isinstance(operational, OperationalWorkflow) else None,
            booking if isinstance(booking, BookingWorkflow) else None,
        ),
    )
    awaiting = None
    if response.intent is not None and response.intent.action == ConversationAction.CONFIRM_HOLD:
        awaiting = "confirmation"
    return ChatMessageResponse(
        thread_id=thread_id,
        message=response.text,
        degraded=response.degraded,
        awaiting=awaiting,
    )
