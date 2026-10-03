"""Migration 013: order edge, revert flag, and settling status columns."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.013")

MIGRATION_ID = "migration_013_propagation_settle"


def migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(propagation_ledger)").fetchall()
    }
    if "after_json" not in cols:
        conn.execute("ALTER TABLE propagation_ledger ADD COLUMN after_json TEXT")
    if "revert_on_fail" not in cols:
        conn.execute(
            "ALTER TABLE propagation_ledger ADD COLUMN revert_on_fail INTEGER NOT NULL DEFAULT 0"
        )
    if "settle_verdict" not in cols:
        conn.execute("ALTER TABLE propagation_ledger ADD COLUMN settle_verdict TEXT")
    conn.commit()
    logger.info("migration 013: propagation settle columns ready")
