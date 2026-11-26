"""Read-only SetuHaul source repository tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dockit.db.connection import connect_source_database
from dockit.db.source_repository import SourceRepository


def test_driver_shipment_queries_return_mapping_rows_and_enforce_ownership(tmp_path: Path) -> None:
    source_path = tmp_path / "setuhaul.db"
    with sqlite3.connect(source_path) as source:
        source.execute(
            """
            CREATE TABLE inbound_snapshot (
                shipment_id TEXT,
                driver_id TEXT,
                destination_facility_id TEXT,
                priority_code TEXT,
                required_dock_type TEXT,
                current_status TEXT,
                effective_eta_ts TEXT,
                appointment_id TEXT,
                slot_id TEXT,
                slot_start_ts TEXT,
                slot_end_ts TEXT,
                planned_dock_code TEXT,
                gate_in_ts TEXT,
                queue_state TEXT,
                queue_position INTEGER
            )
            """
        )
        source.execute(
            """
            INSERT INTO inbound_snapshot VALUES (
                'SHP002', 'DRV001', 'FAC001', 'HIGH', 'STANDARD', 'IN_TRANSIT',
                '2026-08-04T10:00:00+05:30', 'APT002', 'SLOT002',
                '2026-08-04T10:30:00+05:30', '2026-08-04T11:30:00+05:30',
                'D2', NULL, NULL, NULL
            )
            """
        )
        source.execute(
            """
            INSERT INTO inbound_snapshot VALUES (
                'SHP001', 'DRV001', 'FAC001', 'NORMAL', 'STANDARD', 'WAITING',
                '2026-08-04T09:00:00+05:30', NULL, NULL, NULL, NULL, NULL,
                '2026-08-04T09:10:00+05:30', 'WAITING_LATE', 2
            )
            """
        )
        source.execute(
            """
            INSERT INTO inbound_snapshot VALUES (
                'SHP003', 'DRV002', 'FAC001', 'LOW', 'STANDARD', 'ASSIGNED',
                '2026-08-04T12:00:00+05:30', NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL
            )
            """
        )
        source.execute("CREATE VIEW v_inbound_operational_state AS SELECT * FROM inbound_snapshot")

    connection, _ = connect_source_database(source_path)
    try:
        repository = SourceRepository(connection)
        summaries = repository.list_driver_shipment_summaries("DRV001")

        assert [summary["shipment_id"] for summary in summaries] == ["SHP001", "SHP002"]
        assert summaries[1]["effective_eta_ts"] == "2026-08-04T10:00:00+05:30"
        assert summaries[1]["appointment_id"] == "APT002"
        assert summaries[1]["slot_id"] == "SLOT002"
        assert repository.get_driver_shipment_snapshot("DRV001", "SHP002") is not None
        assert repository.get_driver_shipment_snapshot("DRV001", "SHP003") is None
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM inbound_snapshot")
    finally:
        connection.close()
