"""Migration 078: ``dominant_lane`` on ``session_journals`` (R7b).

Stamps the continuity lane that owns a sealed window so tape render can
derive binding after JSONL prune without touch counting.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.078")


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE ADD COLUMN for dominant_lane."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(session_journals)")}
    if "dominant_lane" in existing:
        logger.info("migration 078: session_journals.dominant_lane already present; skip")
        return
    conn.execute("ALTER TABLE session_journals ADD COLUMN dominant_lane TEXT")
    logger.info("migration 078: session_journals.dominant_lane added")
