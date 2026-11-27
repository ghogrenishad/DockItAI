"""Reusable protected-route dependencies; public routes remain explicitly unauthenticated."""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dockit.services.security import AuthenticationError, AuthorizationService, Principal

_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)]


def require_principal(
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
