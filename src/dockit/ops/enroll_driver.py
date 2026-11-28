"""Enroll a Cognito subject against an already-approved pilot driver email."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError

from dockit.core.config import BASE_DIR, Settings, get_settings
from dockit.db.connection import connect_state_database
from dockit.db.migrations import MigrationRunner
from dockit.schemas.contracts import EntityId
from dockit.services.security import AuthorizationError, AuthorizationService

_entity_id = TypeAdapter(EntityId)


class _EnrollmentOnlyVerifier:
    """Prevent this administrative command from being repurposed for authentication."""

    def verify(self, token: str) -> dict[str, object]:
        del token
        raise RuntimeError("the enrollment command cannot authenticate access tokens")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Link a Cognito subject to a unique, pre-approved driver email."
    )
    parser.add_argument("--subject", required=True, help="Cognito sub claim for the pilot user")
    parser.add_argument(
        "--email",
        help="Approved driver email; omit to enter it without placing it in shell history",
    )
    return parser.parse_args(argv)


def enroll_driver(
    *,
    settings: Settings,
    subject: str,
    email: str,
    enrolled_at: datetime | None = None,
) -> str:
    """Apply pending unified migrations and link one subject through the email boundary."""

    if not settings.unified_authoritative_database:
        raise ValueError("driver enrollment requires the unified authoritative database")
    validated_subject = _entity_id.validate_python(subject)
    clean_email = email.strip()
    if not clean_email or len(clean_email) > 254 or "@" not in clean_email:
        raise ValueError("email must be a valid non-empty address")
    connection = connect_state_database(
        settings.state_database_path,
        source_database_path=settings.source_database_path,
        unified_authoritative_database=True,
    )
    try:
        MigrationRunner(BASE_DIR / "migrations").apply(connection)
        return AuthorizationService(connection, _EnrollmentOnlyVerifier()).enroll_driver(
            subject=validated_subject,
            email=clean_email,
            linked_at=enrolled_at or datetime.now(UTC),
        )
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> None:
    """Run the local administrator enrollment command without printing email data."""

    arguments = _arguments(argv)
    email = arguments.email or input("Approved driver email: ")
    try:
        driver_id = enroll_driver(
            settings=get_settings(),
            subject=arguments.subject,
            email=email,
        )
    except (AuthorizationError, ValidationError, ValueError) as error:
        raise SystemExit(f"driver enrollment failed: {error}") from error
    print(f"driver identity enrollment complete for {driver_id}")


if __name__ == "__main__":
    main(sys.argv[1:])
