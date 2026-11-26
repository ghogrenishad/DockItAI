"""Typed, environment-driven configuration for the DockIt service."""

from __future__ import annotations

from enum import StrEnum
from functools import cached_property
from pathlib import Path

from pydantic import AliasChoices, Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]


class Environment(StrEnum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Settings supplied by the environment or the project's optional ``.env`` file.

    The operational source database is intentionally distinct from the DockIt-owned
    state database. Future persistence work may read the former, but may only write
    to the latter.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        validation_alias=AliasChoices("DOCKIT_ENV", "DOCKIT_ENVIRONMENT"),
    )
    source_database_path: Path = Field(
        default=Path("data/dockit.db"),
        validation_alias=AliasChoices("DOCKIT_SOURCE_DATABASE_PATH", "DOCKIT_DATABASE_PATH"),
    )
    state_database_path: Path = Field(
        default=Path("data/dockit_state.db"),
        validation_alias="DOCKIT_STATE_DATABASE_PATH",
    )
    unified_authoritative_database: bool = Field(
        default=True,
        validation_alias="DOCKIT_UNIFIED_AUTHORITATIVE_DATABASE",
    )

    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DOCKIT_REDIS_URL", "UPSTASH_REDIS_REST_URL"),
    )
    redis_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DOCKIT_REDIS_TOKEN", "UPSTASH_REDIS_REST_TOKEN"),
    )

    cognito_region: str | None = Field(default=None, validation_alias="COGNITO_REGION")
    cognito_user_pool_id: str | None = Field(default=None, validation_alias="COGNITO_USER_POOL_ID")
    cognito_app_client_id: str | None = Field(
        default=None,
        validation_alias="COGNITO_APP_CLIENT_ID",
    )

    aws_region: str | None = Field(default=None, validation_alias="AWS_REGION")
    agentcore_runtime_arn: str | None = Field(
        default=None,
        validation_alias="AGENTCORE_RUNTIME_ARN",
    )

    log_level: str = Field(default="INFO", validation_alias="DOCKIT_LOG_LEVEL")
    log_json: bool = Field(default=True, validation_alias="DOCKIT_LOG_JSON")
    request_timeout_seconds: PositiveInt = Field(
        default=30,
        validation_alias="DOCKIT_REQUEST_TIMEOUT_SECONDS",
    )
    request_body_max_bytes: PositiveInt = Field(
        default=1_048_576,
        validation_alias="DOCKIT_REQUEST_BODY_MAX_BYTES",
    )
    server_host: str = Field(default="127.0.0.1", validation_alias="DOCKIT_SERVER_HOST")
    server_port: PositiveInt = Field(default=8000, validation_alias="DOCKIT_SERVER_PORT")

    @model_validator(mode="after")
    def validate_deployment_settings(self) -> Settings:
        self.source_database_path = self._absolute_path(self.source_database_path)
        self.state_database_path = self._absolute_path(self.state_database_path)

        if self.unified_authoritative_database:
            self.state_database_path = self.source_database_path
        elif self.source_database_path == self.state_database_path:
            raise ValueError("source and state database paths must be different")

        if self.environment is Environment.PRODUCTION:
            required = {
                "COGNITO_REGION": self.cognito_region,
                "COGNITO_USER_POOL_ID": self.cognito_user_pool_id,
                "COGNITO_APP_CLIENT_ID": self.cognito_app_client_id,
                "AWS_REGION": self.aws_region,
                "AGENTCORE_RUNTIME_ARN": self.agentcore_runtime_arn,
            }
            missing = sorted(name for name, value in required.items() if not value)
            if missing:
                raise ValueError(
                    "production requires configuration for: " + ", ".join(missing)
                )
        return self

    @staticmethod
    def _absolute_path(path: Path) -> Path:
        return path if path.is_absolute() else (BASE_DIR / path).resolve()

    @cached_property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


def get_settings() -> Settings:
    """Create settings at application startup instead of import time."""

    return Settings()
