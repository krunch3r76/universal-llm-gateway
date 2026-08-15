"""Migration 011: durable commit-to-activation validation history."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.011")
MIGRATION_ID = "migration_011_propagation_validation"


def migrate(conn: sqlite3.Connection) -> None:
    """Create the companion history table without changing attempt status."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS propagation_validation (
          validation_id TEXT PRIMARY KEY,
          row_id TEXT,
          service TEXT NOT NULL,
          code_ref TEXT NOT NULL,
          restart_intent TEXT,
          restart_boundary_monotonic REAL,
          pre_observation TEXT,
          post_observation TEXT,
          observed_code_version TEXT,
          code_ref_relation TEXT,
          identity_measurement TEXT,
          outcome TEXT NOT NULL,
          failure_reason TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_propagation_validation_current
        ON propagation_validation(service, code_ref, updated_at DESC)
        """
    )
    conn.commit()
    logger.info("migration 011: propagation validation table ready")
