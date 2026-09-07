"""Migration 076: succession + uuid columns on ``session_journals``.

Adds ``closed_by``, ``sealed_by``, ``sealed_on``, and ``conversation_uuid`` for
human-continuity speech-tape (todo:human-continuity-speech-tape, A-1/B4).
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.076")

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("closed_by", "TEXT"),
    ("sealed_by", "TEXT"),
    ("sealed_on", "TEXT"),
    ("conversation_uuid", "TEXT"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE ADD COLUMN for each succession field."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(session_journals)")}
    for name, col_type in _COLUMNS:
        if name in existing:
            logger.info(
                "migration 076: session_journals.%s already present; skip", name
            )
            continue
        conn.execute(
            f"ALTER TABLE session_journals ADD COLUMN {name} {col_type}"
        )
        logger.info("migration 076: session_journals.%s added", name)
