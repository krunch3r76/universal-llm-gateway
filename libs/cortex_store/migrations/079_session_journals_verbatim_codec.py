"""Migration 079: ``verbatim_codec`` on ``session_journals`` (messages-v1 seal)."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.079")


def migrate(conn: sqlite3.Connection) -> None:
    """Add ``verbatim_codec`` and backfill legacy rows as ``md-v1``."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(session_journals)")}
    if "verbatim_codec" not in existing:
        conn.execute(
            "ALTER TABLE session_journals ADD COLUMN verbatim_codec TEXT"
        )
        logger.info("migration 079: session_journals.verbatim_codec added")
    else:
        logger.info("migration 079: session_journals.verbatim_codec already present")
    conn.execute(
        "UPDATE session_journals SET verbatim_codec = 'md-v1' "
        "WHERE verbatim_codec IS NULL"
    )
    logger.info("migration 079: backfilled md-v1 on NULL verbatim_codec rows")
