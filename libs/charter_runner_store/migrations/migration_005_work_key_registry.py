"""Migration 005: durable work-key registry for identical-work refire gate."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.005")

MIGRATION_ID = "migration_005_work_key_registry"


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS charter_window_work_key (
          work_key TEXT NOT NULL,
          root_id TEXT NOT NULL,
          window_id TEXT NOT NULL,
          dispatch_id TEXT,
          thread_id TEXT,
          admitted_at REAL NOT NULL,
          disposition TEXT,
          disposition_at REAL,
          PRIMARY KEY (work_key, window_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_work_key_open
        ON charter_window_work_key(work_key)
        WHERE disposition IS NULL
        """
    )
    conn.commit()
    logger.info("migration 005: charter_window_work_key ready")
