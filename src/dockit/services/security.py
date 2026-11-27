"""Cognito access-token verification and driver/group authorization boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.request import urlopen

import jwt

from dockit.db.connection import StateDatabaseConnection, immediate_transaction

_ALLOWED_GROUPS = frozenset({"drivers", "warehouse", "operations"})


class AuthenticationError(PermissionError):
    """Raised when a bearer access token cannot establish a trusted principal."""


class AuthorizationError(PermissionError):
    """Raised when a valid principal lacks the required role or ownership."""


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: frozenset[str]
    driver_id: str | None

    def require_group(self, *groups: str) -> None:
        if not self.groups.intersection(groups):
            raise AuthorizationError("principal lacks the required Cognito group")

    def require_driver_ownership(self, driver_id: str) -> None:
        self.require_group("drivers")
        if self.driver_id != driver_id:
            raise AuthorizationError("driver may act only on their linked driver record")


class CognitoAccessTokenVerifier:
    """Validate Cognito access tokens against cached JWKS signing keys."""

    def __init__(self, *, region: str, user_pool_id: str, app_client_id: str) -> None:
        self._issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self._app_client_id = app_client_id
        self._keys: dict[str, Any] | None = None

    def verify(self, token: str) -> dict[str, object]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as error:
            raise AuthenticationError("token header is invalid") from error
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationError("token must use an RS256 Cognito signing key")
        try:
            key = self._jwks().get(header["kid"])
        except (AuthenticationError, KeyError, OSError, TypeError, ValueError) as error:
            raise AuthenticationError("Cognito signing keys are unavailable") from error
        if key is None:
            self._keys = None
            try:
                key = self._jwks().get(header["kid"])
            except (AuthenticationError, KeyError, OSError, TypeError, ValueError) as error:
                raise AuthenticationError("Cognito signing keys are unavailable") from error
        if key is None:
            raise AuthenticationError("token signing key is unknown")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self._issuer,
                options={"require": ["exp", "iss", "sub", "token_use", "client_id"]},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("Cognito access token is invalid") from error
        if claims.get("token_use") != "access" or claims.get("client_id") != self._app_client_id:
            raise AuthenticationError("token is not issued for this API client")
        return {str(key): value for key, value in claims.items()}

    def _jwks(self) -> dict[str, Any]:
        if self._keys is None:
            try:
                with urlopen(  # noqa: S310
                    f"{self._issuer}/.well-known/jwks.json",
                    timeout=5,
                ) as response:
                    payload = json.load(response)
                keys = payload.get("keys")
                if not isinstance(keys, list):
                    raise AuthenticationError("Cognito JWKS response is invalid")
                self._keys = {
                    str(item["kid"]): jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
                    for item in keys
                    if isinstance(item, dict) and isinstance(item.get("kid"), str)
                }
            except AuthenticationError:
                raise
            except (KeyError, OSError, TypeError, ValueError) as error:
                raise AuthenticationError("Cognito JWKS response is invalid") from error
        return self._keys


class AuthorizationService:
    """Map a validated Cognito subject to a driver and enforce group/ownership rules."""

    def __init__(self, connection: StateDatabaseConnection, verifier: TokenVerifier) -> None:
        self._connection = connection
        self._verifier = verifier

    def authenticate(self, bearer_token: str) -> Principal:
        claims = self._verifier.verify(bearer_token)
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("token has no Cognito subject")
        raw_groups = claims.get("cognito:groups", [])
        if not isinstance(raw_groups, list) or not all(
            isinstance(group, str) for group in raw_groups
        ):
            raise AuthenticationError("token groups are invalid")
        groups = frozenset(raw_groups).intersection(_ALLOWED_GROUPS)
        if not groups:
            raise AuthorizationError("principal has no recognized DockIt group")
        row = self._connection.execute(
            "SELECT driver_id FROM driver_identity_links WHERE cognito_subject = ?", (subject,)
        ).fetchone()
        return Principal(subject, groups, str(row[0]) if row is not None else None)

    def enroll_driver(self, *, subject: str, email: str, linked_at: datetime) -> str:
        """Link a Cognito subject to a unique source driver matched by enrolled email."""

        timestamp = linked_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        with immediate_transaction(self._connection):
            driver = self._connection.execute(
                "SELECT driver_id FROM drivers WHERE email = ?", (email,)
            ).fetchone()
            if driver is None:
                raise AuthorizationError("email is not enrolled for a SetuHaul driver")
            self._connection.execute(
                """
                INSERT INTO driver_identity_links (cognito_subject, driver_id, linked_at_utc)
                VALUES (?, ?, ?)
                """,
                (subject, driver[0], timestamp),
            )
            return str(driver[0])
