"""Migration 009: persist allow_self_preempt and force on propagation ledger open rows."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.009")

MIGRATION_ID = "migration_009_propagation_force_flags"


def migrate(conn: sqlite3.Connection) -> None:
    """Add allow_self_preempt and force INTEGER columns with model-aligned defaults."""
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(propagation_ledger)").fetchall()
    }
    if "allow_self_preempt" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN allow_self_preempt INTEGER NOT NULL DEFAULT 1
            """
        )
    if "force" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN force INTEGER NOT NULL DEFAULT 0
            """
        )
    conn.commit()
    logger.info("migration 009: propagation force flags columns ready")
