"""Configuration boundary tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dockit.core.config import BASE_DIR, Environment, Settings


def test_database_paths_are_resolved_and_separate() -> None:
    settings = Settings(
        DOCKIT_SOURCE_DATABASE_PATH="fixtures/source.db",
        DOCKIT_STATE_DATABASE_PATH="fixtures/state.db",
    )

    assert settings.source_database_path == BASE_DIR / "fixtures/source.db"
    assert settings.state_database_path == BASE_DIR / "fixtures/state.db"


def test_default_source_path_matches_the_supplied_setuhaul_snapshot() -> None:
    settings = Settings()

    assert settings.source_database_path == BASE_DIR / "data/dockit.db"


def test_same_source_and_state_database_is_rejected(tmp_path: Path) -> None:
    database_path = tmp_path / "shared.db"

    with pytest.raises(ValidationError, match="must be different"):
        Settings(
            DOCKIT_SOURCE_DATABASE_PATH=database_path,
            DOCKIT_STATE_DATABASE_PATH=database_path,
        )


def test_production_requires_future_identity_and_runtime_configuration() -> None:
    with pytest.raises(ValidationError, match="COGNITO_APP_CLIENT_ID"):
        Settings(DOCKIT_ENV=Environment.PRODUCTION)


def test_production_configuration_is_accepted_when_complete() -> None:
    settings = Settings(
        DOCKIT_ENV=Environment.PRODUCTION,
        COGNITO_REGION="ap-south-1",
        COGNITO_USER_POOL_ID="ap-south-1_example",
        COGNITO_APP_CLIENT_ID="example-client",
        AWS_REGION="ap-south-1",
        AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:ap-south-1:123456789012:runtime/x",
    )

    assert settings.is_production is True
