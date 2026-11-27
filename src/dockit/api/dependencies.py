"""Reusable protected-route dependencies; public routes remain explicitly unauthenticated."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dockit.db.source_repository import SourceRepository
from dockit.services.booking import BookingWorkflow
from dockit.services.security import (
    AuthenticationError,
    AuthorizationError,
    AuthorizationService,
    Principal,
)
from dockit.services.workflows import OperationalWorkflow

_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)]


async def require_principal(
    request: Request,
    credentials: BearerCredentials,
) -> Principal:
    """Verify a Cognito access token for a protected future route."""

    service = getattr(request.app.state, "authorization_service", None)
    if not isinstance(service, AuthorizationService):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication is unavailable")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer access token required")
    try:
        return service.authenticate(credentials.credentials)
    except AuthenticationError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
    except AuthorizationError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error


DriverPrincipal = Annotated[Principal, Depends(require_principal)]


def require_driver_principal(principal: DriverPrincipal) -> Principal:
    """Require a Cognito principal linked to a driver for driver-facing routes."""

    try:
        principal.require_group("drivers")
        if principal.driver_id is None:
            raise AuthorizationError("driver identity is not enrolled")
    except AuthorizationError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(error)) from error
    return principal


def get_source_repository(request: Request) -> SourceRepository:
    """Return the read-only source repository opened by the application lifespan."""

    repository = getattr(request.app.state, "source_repository", None)
    if not isinstance(repository, SourceRepository):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "source data is unavailable")
    return repository


def get_operational_workflow(request: Request) -> OperationalWorkflow:
    """Return the authoritative workflow service when unified mode is enabled."""

    workflow = getattr(request.app.state, "operational_workflow", None)
    if not isinstance(workflow, OperationalWorkflow):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "operational workflow is unavailable",
        )
    return workflow


def get_booking_workflow(request: Request) -> BookingWorkflow:
    """Return the authoritative booking service when unified mode is enabled."""

    workflow = getattr(request.app.state, "booking_workflow", None)
    if not isinstance(workflow, BookingWorkflow):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "booking workflow is unavailable")
    return workflow
