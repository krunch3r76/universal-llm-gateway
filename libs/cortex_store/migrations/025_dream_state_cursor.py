"""Migration 025: Dream state cursor — consolidation pipeline state.

Adds the dream_state_cursor table for tracking the dream state consolidation
pipeline's progress through the assertion set. Enables incremental processing
so each run only assesses assertions newer than the previous cursor position.

Origin: Agent bus thread 461, Phase D1 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("cortex-api.migration.025")


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dream_state_cursor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_processed_id INTEGER,
            run_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            pipeline_version TEXT NOT NULL,
            assertions_processed INTEGER NOT NULL DEFAULT 0,
            actions_taken INTEGER NOT NULL DEFAULT 0
        )
    """)

    logger.info("Migration 025 (dream_state_cursor) complete")
