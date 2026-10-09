"""Migration 081: one session_journals row per non-null session_id.

The head schema template already carries this index. This migration installs
it on databases created before that template, and deletes extra rows first.
The keeper is the lowest id. Extra rows are removed, not merged.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.081")


def migrate(conn: sqlite3.Connection) -> None:
    """Dedupe session_id, then create the partial unique index."""
    dupes = conn.execute(
        "SELECT session_id, COUNT(*) AS n FROM session_journals "
        "WHERE session_id IS NOT NULL GROUP BY session_id HAVING n > 1"
    ).fetchall()
    for row in dupes:
        session_id = row[0]
        deleted = conn.execute(
            "DELETE FROM session_journals WHERE session_id = ? AND id NOT IN ("
            "SELECT MIN(id) FROM session_journals WHERE session_id = ?"
            ")",
            (session_id, session_id),
        ).rowcount
        logger.warning(
            "migration 081: removed %s duplicate session_journals rows for %s",
            deleted,
            session_id,
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_journals_session_id_unique "
        "ON session_journals(session_id) WHERE session_id IS NOT NULL"
    )
    logger.info("migration 081: session_id unique index ensured")
