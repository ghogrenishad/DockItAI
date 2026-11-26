"""SQLite connection boundaries for source data and DockIt-owned state."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUSY_TIMEOUT_MS = 5_000


class StateDatabaseConnection(sqlite3.Connection):
    """A SQLite connection created for DockIt-owned writable state only."""

    source_database_path: Path
    state_database_path: Path


class SourceDatabaseConnection(sqlite3.Connection):
    """A SQLite connection opened against the read-only SetuHaul snapshot."""


@dataclass(frozen=True)
class SourceColumn:
    """A column described by SQLite's schema metadata."""

    name: str
    declared_type: str
    not_null: bool
    default_value: str | None
    primary_key_position: int


class SourceCatalog:
    """Safe, schema-only inspection of an operational source database.

    The source database is owned by SetuHaul. The catalog exposes only its
    declared SQLite metadata; domain reads belong in the read-only repository.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def table_names(self) -> tuple[str, ...]:
        """Return user-defined table names in stable order."""

        rows = self._connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def view_names(self) -> tuple[str, ...]:
        """Return user-defined view names in stable order."""

        rows = self._connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'view' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def relation_names(self) -> tuple[str, ...]:
        """Return all supported source relations in stable order."""

        return tuple(sorted((*self.table_names(), *self.view_names())))

    def columns(self, relation_name: str) -> tuple[SourceColumn, ...]:
        """Return columns for an existing source table or view without accepting SQL."""

        if relation_name not in self.relation_names():
            raise ValueError(f"unknown source table or view: {relation_name!r}")

        quoted_name = '"' + relation_name.replace('"', '""') + '"'
        rows = self._connection.execute(f"PRAGMA table_info({quoted_name})").fetchall()
        return tuple(
            SourceColumn(
                name=str(row[1]),
                declared_type=str(row[2]),
                not_null=bool(row[3]),
                default_value=str(row[4]) if row[4] is not None else None,
                primary_key_position=int(row[5]),
            )
            for row in rows
        )


def connect_source_database(
    database_path: Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> tuple[SourceDatabaseConnection, SourceCatalog]:
    """Open the SetuHaul database in SQLite read-only mode.

    SQLite's ``mode=ro`` prevents writes and DDL at the file boundary. The
    additional ``query_only`` pragma protects this connection if it is ever
    repointed by an embedding application. Callers receive a catalog rather
    than a raw query API because source-domain assumptions are still unknown.
    """

    if not database_path.is_file():
        raise FileNotFoundError(f"source database does not exist: {database_path}")
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be non-negative")

    source_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(
        source_uri,
        uri=True,
        isolation_level=None,
        factory=SourceDatabaseConnection,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA query_only = ON")
        return connection, SourceCatalog(connection)
    except BaseException:
        connection.close()
        raise


def connect_state_database(
    database_path: Path,
    *,
    source_database_path: Path,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> StateDatabaseConnection:
    """Open DockIt's writable state database with its required SQLite rules."""

    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be non-negative")

    canonical_source_path = source_database_path.resolve()
    canonical_state_path = database_path.resolve()
    if canonical_state_path == canonical_source_path:
        raise ValueError("state database path must be different from source database path")

    canonical_state_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        canonical_state_path,
        isolation_level=None,
        factory=StateDatabaseConnection,
    )
    try:
        connection.source_database_path = canonical_source_path
        connection.state_database_path = canonical_state_path
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys_enabled is None or foreign_keys_enabled[0] != 1:
            raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "wal":
            raise RuntimeError("SQLite WAL mode could not be enabled for state database")
        return connection
    except BaseException:
        connection.close()
        raise


def is_state_database_connection(connection: sqlite3.Connection) -> bool:
    """Return whether a connection was bound to distinct DockIt state paths."""

    if not isinstance(connection, StateDatabaseConnection):
        return False
    try:
        database_rows = connection.execute("PRAGMA database_list").fetchall()
        main_database_path = next(
            Path(str(row[2])).resolve() for row in database_rows if row[1] == "main"
        )
        return (
            connection.source_database_path != connection.state_database_path
            and connection.state_database_path == main_database_path
        )
    except (AttributeError, sqlite3.Error, StopIteration):
        return False


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """Run a rollback-safe transaction that reserves the SQLite write lock early."""

    if connection.in_transaction:
        raise RuntimeError("BEGIN IMMEDIATE transaction cannot be nested")

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        try:
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
