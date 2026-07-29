"""Migration 004: durable open propagation rows for harvest closure."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.004")

MIGRATION_ID = "migration_004_propagation_ledger"


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS propagation_ledger (
          row_id TEXT PRIMARY KEY,
          service TEXT NOT NULL,
          action TEXT NOT NULL,
          code_ref TEXT NOT NULL,
          safe_window TEXT NOT NULL,
          hazard TEXT,
          reason TEXT,
          proof TEXT NOT NULL,
          proof_class TEXT NOT NULL,
          mint_thread TEXT,
          mint_turn INTEGER,
          status TEXT NOT NULL DEFAULT 'open',
          age_in_harvests INTEGER NOT NULL DEFAULT 0,
          defer_reason TEXT,
          proof_payload TEXT,
          closed_at REAL,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_propagation_ledger_open
        ON propagation_ledger(status, service)
        """
    )
    conn.commit()
    logger.info("migration 004: propagation_ledger ready")
