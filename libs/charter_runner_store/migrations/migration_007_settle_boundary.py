"""Migration 007: persist settle boundary monotonic on propagation ledger rows."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.007")

MIGRATION_ID = "migration_007_settle_boundary"


def migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(propagation_ledger)").fetchall()
    }
    if "settle_boundary_monotonic" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN settle_boundary_monotonic REAL
            """
        )
    conn.commit()
    logger.info("migration 007: settle_boundary_monotonic column ready")
