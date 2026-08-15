"""Migration 012: activation pending indexes and kill-boundary columns."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.012")
MIGRATION_ID = "migration_012_validation_pending"


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """Add kill-boundary columns and partial unique index for pending validations."""
    cols = _column_names(conn, "propagation_validation")
    if "kill_boundary_at" not in cols:
        conn.execute(
            "ALTER TABLE propagation_validation ADD COLUMN kill_boundary_at TEXT"
        )
    if "boundary_source" not in cols:
        conn.execute(
            "ALTER TABLE propagation_validation ADD COLUMN boundary_source TEXT"
        )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_propagation_validation_pending
        ON propagation_validation(service, code_ref)
        WHERE outcome='pending'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_propagation_validation_intent
        ON propagation_validation(restart_intent)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_propagation_validation_pending_row
        ON propagation_validation(row_id)
        WHERE outcome='pending' AND row_id IS NOT NULL
        """
    )
    conn.commit()
    logger.info("migration 012: validation pending indexes ready")
