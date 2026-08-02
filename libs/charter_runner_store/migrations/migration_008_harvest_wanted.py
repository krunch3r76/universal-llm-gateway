"""Migration 008: exactly-once consumption claim for harvest-wanted propagation rows."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.008")

MIGRATION_ID = "migration_008_harvest_wanted"


def migrate(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(propagation_ledger)").fetchall()
    }
    if "consumption_token" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN consumption_token TEXT
            """
        )
    if "consumption_claimed_at" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN consumption_claimed_at REAL
            """
        )
    conn.commit()
    logger.info("migration 008: harvest_wanted consumption claim columns ready")
