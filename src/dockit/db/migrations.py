"""Ordered, checksum-verified migrations for DockIt-owned SQLite state."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dockit.db.connection import immediate_transaction, is_state_database_connection

_MIGRATION_FILENAME = re.compile(r"^(?P<version>[1-9][0-9]*)_(?P<name>[a-z0-9_]+)\.sql$")
_TRANSACTION_KEYWORDS = frozenset({"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"})
_LEADING_SQL_COMMENTS = re.compile(
    r"\A(?:\s+|--[^\r\n]*(?:\r?\n|$)|/\*.*?\*/)*",
    re.DOTALL,
)
_UNIFIED_DIRECTIVE = "-- dockit: unified-authoritative"


class MigrationError(RuntimeError):
    """Raised when a migration directory or state database is inconsistent."""


@dataclass(frozen=True)
class Migration:
    """An immutable SQL migration discovered from a directory."""

    version: int
    name: str
    path: Path
    checksum: str
    sql: str


class MigrationRunner:
    """Apply additive, versioned SQL migrations to a state connection only."""

    def __init__(self, migration_directory: Path) -> None:
        self._migration_directory = migration_directory

    def discover(self) -> tuple[Migration, ...]:
        """Load valid migration files, rejecting ambiguous or malformed names."""

        if not self._migration_directory.is_dir():
            raise MigrationError(f"migration directory does not exist: {self._migration_directory}")

        migrations: list[Migration] = []
        seen_versions: set[int] = set()
        entries = sorted(self._migration_directory.iterdir(), key=lambda candidate: candidate.name)
        for path in entries:
            if path.name == "README.md" or path.name.startswith("."):
                continue
            if not path.is_file():
                raise MigrationError(f"migration entry is not a file: {path.name}")
            match = _MIGRATION_FILENAME.fullmatch(path.name)
            if match is None:
                raise MigrationError(
                    "migration names must use N_name.sql with a positive integer N: "
                    f"{path.name}"
                )
            version = int(match["version"])
            if version in seen_versions:
                raise MigrationError(f"duplicate migration version: {version}")
            seen_versions.add(version)
            sql = path.read_text(encoding="utf-8")
            migrations.append(
                Migration(
                    version=version,
                    name=match["name"],
                    path=path,
                    checksum=hashlib.sha256(sql.encode()).hexdigest(),
                    sql=sql,
                )
            )
        return tuple(sorted(migrations, key=lambda migration: migration.version))

    def apply(self, connection: sqlite3.Connection) -> tuple[Migration, ...]:
        """Validate migration history and atomically apply each pending migration."""

        if not is_state_database_connection(connection):
            raise MigrationError("migrations require a DockIt state database connection")
        migrations = tuple(
            migration
            for migration in self.discover()
            if getattr(connection, "unified_authoritative_database", False)
            or not migration.sql.lstrip().startswith(_UNIFIED_DIRECTIVE)
        )
        self._create_tracking_table(connection)
        applied = self._load_applied(connection)
        discovered_by_version = {migration.version: migration for migration in migrations}

        for version, (name, checksum) in applied.items():
            migration = discovered_by_version.get(version)
            if migration is None:
                raise MigrationError(f"applied migration {version}_{name} is missing from disk")
            if migration.name != name or migration.checksum != checksum:
                raise MigrationError(f"migration checksum drift detected for version {version}")

        newly_applied: list[Migration] = []
        highest_applied_version = max(applied, default=0)
        for migration in migrations:
            if migration.version in applied:
                continue
            if migration.version < highest_applied_version:
                raise MigrationError(
                    "cannot apply migration version "
                    f"{migration.version} after version {highest_applied_version}"
                )
            self._apply_one(connection, migration)
            newly_applied.append(migration)
        return tuple(newly_applied)

    @staticmethod
    def _create_tracking_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS _dockit_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )

    @staticmethod
    def _load_applied(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
        rows = connection.execute(
            "SELECT version, name, checksum FROM _dockit_schema_migrations ORDER BY version"
        ).fetchall()
        return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}

    def _apply_one(self, connection: sqlite3.Connection, migration: Migration) -> None:
        statements = _split_statements(migration.sql, migration.path)
        with immediate_transaction(connection):
            for statement in statements:
                _reject_transaction_control(statement, migration.path)
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO _dockit_schema_migrations (version, name, checksum)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.name, migration.checksum),
            )


def _split_statements(sql: str, path: Path) -> tuple[str, ...]:
    """Split complete SQLite statements without ``executescript`` implicit commits."""

    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError(f"incomplete SQL statement in {path.name}")
    return tuple(statements)


def _reject_transaction_control(statement: str, path: Path) -> None:
    """Keep transaction ownership with the migration runner."""

    without_comments = _LEADING_SQL_COMMENTS.sub("", statement)
    keyword_match = re.match(r"[A-Za-z]+", without_comments)
    keyword = keyword_match.group(0).upper() if keyword_match is not None else ""
    if keyword in _TRANSACTION_KEYWORDS:
        raise MigrationError(f"transaction control is not allowed in {path.name}")
