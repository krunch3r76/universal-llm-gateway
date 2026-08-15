"""Migration 010: persist proof_class_requested on propagation ledger rows."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.010")

MIGRATION_ID = "migration_010_propagation_proof_class_requested"


def migrate(conn: sqlite3.Connection) -> None:
    """Add proof_class_requested so an audit can read it after the envelope is gone."""
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(propagation_ledger)").fetchall()
    }
    if "proof_class_requested" not in cols:
        conn.execute(
            """
            ALTER TABLE propagation_ledger
            ADD COLUMN proof_class_requested TEXT
            """
        )
    conn.commit()
    logger.info("migration 010: proof_class_requested column ready")
