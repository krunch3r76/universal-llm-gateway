"""Migration 014: persist first-parent master relation on validation rows."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.014")
MIGRATION_ID = "migration_014_code_ref_master_relation"


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    cols = _column_names(conn, "propagation_validation")
    if "code_ref_master_relation" not in cols:
        conn.execute(
            "ALTER TABLE propagation_validation ADD COLUMN code_ref_master_relation TEXT"
        )
    conn.commit()
    logger.info("migration 014: code_ref_master_relation column ready")
