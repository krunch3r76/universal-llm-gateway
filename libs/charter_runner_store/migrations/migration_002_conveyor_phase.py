"""Migration 002: conveyor_phase + pickup_append_cursor on root_ledger."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.002")

MIGRATION_ID = "migration_002_conveyor_phase"


def migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(root_ledger)").fetchall()
    }
    if "conveyor_phase" not in cols:
        conn.execute(
            """
            ALTER TABLE root_ledger
            ADD COLUMN conveyor_phase TEXT NOT NULL DEFAULT 'dormant'
            """
        )
    if "pickup_append_cursor" not in cols:
        conn.execute(
            """
            ALTER TABLE root_ledger
            ADD COLUMN pickup_append_cursor INTEGER NOT NULL DEFAULT 0
            """
        )
    conn.commit()
    logger.info("migration 002: conveyor_phase columns ready")
