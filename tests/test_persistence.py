"""Persistence-boundary and migration-runner tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dockit.core.config import BASE_DIR
from dockit.db.connection import (
    StateDatabaseConnection,
    connect_source_database,
    connect_state_database,
    immediate_transaction,
)
from dockit.db.migrations import MigrationError, MigrationRunner
from dockit.db.source_repository import SourceRepository
from dockit.db.state_repository import StateRepository


def _connect_state(
    database_path: Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> StateDatabaseConnection:
    return connect_state_database(
        database_path,
        source_database_path=database_path.with_name("setuhaul_source.db"),
        busy_timeout_ms=busy_timeout_ms,
    )


def _create_source_snapshot(tmp_path: Path) -> Path:
    source_path = tmp_path / "setuhaul_source.db"
    schema_sql = (BASE_DIR / "data/setuhaul_schema_and_seed.sql").read_text(encoding="utf-8")
    connection = sqlite3.connect(source_path)
    try:
        connection.executescript(schema_sql)
    finally:
        connection.close()
    return source_path


def test_source_connection_allows_schema_inspection_but_denies_writes(tmp_path: Path) -> None:
    source_path = tmp_path / "setuhaul.db"
    with sqlite3.connect(source_path) as source:
        source.execute("CREATE TABLE shipments (reference TEXT PRIMARY KEY)")
        source.execute("INSERT INTO shipments VALUES ('SH-100')")

    connection, catalog = connect_source_database(source_path)
    try:
        assert catalog.table_names() == ("shipments",)
        assert catalog.columns("shipments")[0].name == "reference"
        with pytest.raises(ValueError, match="unknown source table"):
            catalog.columns("shipments; DROP TABLE shipments")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO shipments VALUES ('SH-101')")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE illicit (id INTEGER)")
    finally:
        connection.close()

    with sqlite3.connect(source_path) as verify:
        assert verify.execute("SELECT reference FROM shipments").fetchall() == [("SH-100",)]


def test_missing_source_database_is_not_created(tmp_path: Path) -> None:
    source_path = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        connect_source_database(source_path)

    assert not source_path.exists()


def test_source_database_cannot_be_opened_or_migrated_as_state(tmp_path: Path) -> None:
    source_path = tmp_path / "setuhaul.db"
    with sqlite3.connect(source_path) as source:
        source.execute("CREATE TABLE shipments (shipment_id TEXT PRIMARY KEY)")

    with pytest.raises(ValueError, match="different from source"):
        connect_state_database(source_path, source_database_path=source_path)

    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "1_create_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    source_connection, _ = connect_source_database(source_path)
    try:
        with pytest.raises(MigrationError, match="state database connection"):
            MigrationRunner(migration_directory).apply(source_connection)
    finally:
        source_connection.close()

    with sqlite3.connect(source_path) as verify:
        assert verify.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall() == [
            ("shipments",)
        ]


def test_state_database_enforces_foreign_keys_wal_and_transaction_rollback(tmp_path: Path) -> None:
    connection = _connect_state(tmp_path / "state.db")
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert connection.execute("PRAGMA busy_timeout").fetchone() == (5_000,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id) NOT NULL)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO child VALUES (99)")

        with pytest.raises(RuntimeError, match="force rollback"):
            with immediate_transaction(connection):
                connection.execute("INSERT INTO parent VALUES (1)")
                raise RuntimeError("force rollback")
        assert connection.execute("SELECT id FROM parent").fetchall() == []
    finally:
        connection.close()


def test_state_repository_rejects_a_generic_sqlite_connection(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "generic.db")
    try:
        with pytest.raises(ValueError, match="state repository requires"):
            StateRepository(connection)  # type: ignore[arg-type]
    finally:
        connection.close()


def test_immediate_transaction_rolls_back_if_commit_fails() -> None:
    class CommitFailingConnection:
        def __init__(self) -> None:
            self.in_transaction = False
            self.rollback_called = False

        def execute(self, statement: str) -> None:
            assert statement == "BEGIN IMMEDIATE"

        def commit(self) -> None:
            raise RuntimeError("commit failed")

        def rollback(self) -> None:
            self.rollback_called = True

    connection = CommitFailingConnection()

    with pytest.raises(RuntimeError, match="commit failed"):
        with immediate_transaction(connection):  # type: ignore[arg-type]
            pass

    assert connection.rollback_called is True


def test_immediate_transaction_blocks_a_second_state_writer(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    first_connection = _connect_state(database_path, busy_timeout_ms=25)
    second_connection = _connect_state(database_path, busy_timeout_ms=25)
    try:
        with immediate_transaction(first_connection):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                with immediate_transaction(second_connection):
                    pass
    finally:
        first_connection.close()
        second_connection.close()


def test_migrations_apply_fresh_then_upgrade_in_order(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "2_add_status.sql").write_text(
        "ALTER TABLE notes ADD COLUMN status TEXT NOT NULL DEFAULT 'new';\n",
        encoding="utf-8",
    )
    (migration_directory / "1_create_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);\n",
        encoding="utf-8",
    )
    connection = _connect_state(tmp_path / "state.db")
    runner = MigrationRunner(migration_directory)
    try:
        assert [migration.version for migration in runner.apply(connection)] == [1, 2]
        assert runner.apply(connection) == ()
        assert connection.execute("PRAGMA table_info(notes)").fetchall()[2][1] == "status"

        (migration_directory / "3_add_index.sql").write_text(
            "CREATE INDEX notes_body_index ON notes(body);\n",
            encoding="utf-8",
        )
        assert [migration.version for migration in runner.apply(connection)] == [3]
    finally:
        connection.close()


def test_migration_checksum_drift_and_partial_failure_are_rejected(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    migration_path = migration_directory / "1_create_notes.sql"
    migration_path.write_text("CREATE TABLE notes (id INTEGER PRIMARY KEY);\n", encoding="utf-8")
    connection = _connect_state(tmp_path / "state.db")
    runner = MigrationRunner(migration_directory)
    try:
        runner.apply(connection)
        migration_path.write_text(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);\n",
            encoding="utf-8",
        )
        with pytest.raises(MigrationError, match="checksum drift"):
            runner.apply(connection)

        (migration_directory / "2_failing.sql").write_text(
            "CREATE TABLE transient_table (id INTEGER PRIMARY KEY);\nINVALID SQL;\n",
            encoding="utf-8",
        )
        migration_path.write_text(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )
        with pytest.raises(sqlite3.OperationalError):
            runner.apply(connection)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'transient_table'"
        ).fetchall() == []
        assert connection.execute(
            "SELECT version FROM _dockit_schema_migrations WHERE version = 2"
        ).fetchall() == []

        (migration_directory / "2_failing.sql").write_text(
            "CREATE TABLE recovered_table (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )
        assert [migration.version for migration in runner.apply(connection)] == [2]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'recovered_table'"
        ).fetchall() == [("recovered_table",)]
    finally:
        connection.close()


def test_migration_runner_rejects_duplicate_versions(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "1_create_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (migration_directory / "1_create_audit.sql").write_text(
        "CREATE TABLE audit (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    connection = _connect_state(tmp_path / "state.db")
    try:
        with pytest.raises(MigrationError, match="duplicate migration version"):
            MigrationRunner(migration_directory).apply(connection)
    finally:
        connection.close()


def test_migrations_reject_connections_outside_the_state_database_factory(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "1_create_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    connection = sqlite3.connect(tmp_path / "untrusted.db")
    try:
        with pytest.raises(MigrationError, match="state database connection"):
            MigrationRunner(migration_directory).apply(connection)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = '_dockit_schema_migrations'"
        ).fetchall() == []
    finally:
        connection.close()


def test_migration_runner_rejects_backfilled_versions(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "1_create_notes.sql").write_text(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    (migration_directory / "3_add_status.sql").write_text(
        "ALTER TABLE notes ADD COLUMN status TEXT;\n",
        encoding="utf-8",
    )
    connection = _connect_state(tmp_path / "state.db")
    runner = MigrationRunner(migration_directory)
    try:
        assert [migration.version for migration in runner.apply(connection)] == [1, 3]
        (migration_directory / "2_backfill.sql").write_text(
            "CREATE TABLE later_discovered (id INTEGER PRIMARY KEY);\n",
            encoding="utf-8",
        )
        with pytest.raises(
            MigrationError,
            match="cannot apply migration version 2 after version 3",
        ):
            runner.apply(connection)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'later_discovered'"
        ).fetchall() == []
    finally:
        connection.close()


def test_migration_runner_rejects_transaction_control_after_a_block_comment(tmp_path: Path) -> None:
    migration_directory = tmp_path / "migrations"
    migration_directory.mkdir()
    (migration_directory / "1_bad_transaction.sql").write_text(
        "CREATE TABLE transient_table (id INTEGER PRIMARY KEY);\n/* docs */ COMMIT;\n",
        encoding="utf-8",
    )
    connection = _connect_state(tmp_path / "state.db")
    try:
        with pytest.raises(MigrationError, match="transaction control"):
            MigrationRunner(migration_directory).apply(connection)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'transient_table'"
        ).fetchall() == []
    finally:
        connection.close()


def test_source_repository_reads_a_reproducible_seed_snapshot(tmp_path: Path) -> None:
    source_path = _create_source_snapshot(tmp_path)
    connection, catalog = connect_source_database(source_path)
    try:
        repository = SourceRepository(connection)

        assert catalog.view_names() == (
            "v_current_facility_queue",
            "v_inbound_operational_state",
            "v_latest_eta",
            "v_slot_availability",
        )
        summaries = repository.list_driver_shipment_summaries("DRV006")
        assert [summary["shipment_id"] for summary in summaries] == ["SHP1006", "SHP1021"]
        assert summaries[0]["effective_eta_ts"] == "2026-08-04T11:20:00+05:30"
        assert summaries[0]["appointment_id"] == "APT1006"
        assert summaries[0]["slot_id"] == "SLOT-JAI-045"
        assert repository.get_driver_shipment_snapshot("DRV006", "SHP1006") == summaries[0]
        assert repository.get_driver_shipment_snapshot("DRV004", "SHP1006") is None
    finally:
        connection.close()

def test_initial_state_migration_isolated_from_source_and_supports_state_repositories(
    tmp_path: Path,
) -> None:
    connection = _connect_state(tmp_path / "state.db")
    try:
        runner = MigrationRunner(BASE_DIR / "migrations")
        assert [migration.version for migration in runner.apply(connection)] == [1, 2, 3]
        assert runner.apply(connection) == ()

        repository = StateRepository(connection)
        with immediate_transaction(connection):
            repository.create_conversation_reference(
                conversation_reference_id="conversation-1",
                principal_subject="driver:DRV006",
                source_thread_id="THR001",
                source_shipment_id="SHP1006",
                created_at_utc="2026-08-04T04:04:00Z",
                last_seen_at_utc="2026-08-04T04:04:00Z",
            )
            repository.create_slot_hold(
                hold_id="hold-1",
                source_shipment_id="SHP1006",
                source_slot_id="SLOT-JAI-060",
                actor_subject="driver:DRV006",
                conversation_reference_id="conversation-1",
                created_at_utc="2026-08-04T04:04:00Z",
                options_provided_at_utc="2026-08-04T04:04:00Z",
                expires_at_utc="2026-08-04T04:09:00Z",
            )
            assert repository.record_hold_reminder(
                hold_id="hold-1",
                reminder_sent_at_utc="2026-08-04T04:06:30Z",
            )
            assert not repository.record_hold_reminder(
                hold_id="hold-1",
                reminder_sent_at_utc="2026-08-04T04:06:31Z",
            )
            repository.record_idempotency_key(
                actor_subject="driver:DRV006",
                operation="create_hold",
                idempotency_key="request-1",
                request_fingerprint="sha256:example",
                created_at_utc="2026-08-04T04:04:00Z",
            )
            repository.append_audit_event(
                audit_event_id="audit-1",
                occurred_at_utc="2026-08-04T04:04:00Z",
                actor_subject="driver:DRV006",
                event_type="hold.created",
                entity_type="source_slot",
                entity_id="SLOT-JAI-060",
                correlation_id="00000000-0000-0000-0000-000000000001",
                payload_json="{}",
                state_hold_id="hold-1",
            )

        assert connection.execute("SELECT COUNT(*) FROM conversation_references").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM slot_holds").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM idempotency_records").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (1,)
        assert connection.execute(
            "SELECT options_provided_at_utc, reminder_sent_at_utc "
            "FROM slot_holds WHERE hold_id = 'hold-1'"
        ).fetchone() == ("2026-08-04T04:04:00Z", "2026-08-04T04:06:30Z")

        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            with immediate_transaction(connection):
                repository.create_slot_hold(
                    hold_id="hold-2",
                    source_shipment_id="SHP1010",
                    source_slot_id="SLOT-JAI-060",
                    actor_subject="driver:DRV010",
                    conversation_reference_id=None,
                    created_at_utc="2026-08-04T04:05:00Z",
                    options_provided_at_utc="2026-08-04T04:05:00Z",
                    expires_at_utc="2026-08-04T04:10:00Z",
                )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            with immediate_transaction(connection):
                repository.append_audit_event(
                    audit_event_id="audit-2",
                    occurred_at_utc="2026-08-04T04:05:00Z",
                    actor_subject="driver:DRV006",
                    event_type="hold.checked",
                    entity_type="source_slot",
                    entity_id="SLOT-JAI-060",
                    correlation_id=None,
                    payload_json="{}",
                    state_hold_id="missing-hold",
                )
    finally:
        connection.close()


def test_state_database_serializes_competing_writers(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    first_connection = _connect_state(database_path, busy_timeout_ms=50)
    second_connection = _connect_state(database_path, busy_timeout_ms=50)
    try:
        with immediate_transaction(first_connection):
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                with immediate_transaction(second_connection):
                    pass
    finally:
        second_connection.close()
        first_connection.close()
