"""Migration 081: one session_journals row per non-null session_id.

The head schema template already carries this index. This migration installs
it on databases created before that template. Duplicate non-null session_ids
are refused: the database is left unchanged and the index is not created.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.081")

_LIST_CAP = 50


class Migration081DuplicatesError(RuntimeError):
    """Raised when non-null session_id values are duplicated.

    The migration runner has not recorded version 081, and no rows were
    deleted. cortex-api startup re-raises this and refuses to serve.
    """

    def __init__(self, session_ids: list[str]) -> None:
        self.session_ids = list(session_ids)
        shown = self.session_ids[:_LIST_CAP]
        cap = ""
        if len(self.session_ids) > _LIST_CAP:
            cap = f" (listing capped at {_LIST_CAP} of {len(self.session_ids)})"
        super().__init__(
            "migration 081: "
            f"{len(self.session_ids)} duplicated session_id values{cap}: {shown}"
        )


def migrate(conn: sqlite3.Connection) -> None:
    """Create the partial unique index, or refuse when duplicates exist."""
    dupes = conn.execute(
        "SELECT session_id, COUNT(*) AS n FROM session_journals "
        "WHERE session_id IS NOT NULL GROUP BY session_id HAVING n > 1 "
        "ORDER BY session_id"
    ).fetchall()
    if dupes:
        session_ids = [str(row[0]) for row in dupes]
        logger.error(
            "migration 081: %s duplicated session_id values; index not created",
            len(session_ids),
        )
        raise Migration081DuplicatesError(session_ids)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_journals_session_id_unique "
        "ON session_journals(session_id) WHERE session_id IS NOT NULL"
    )
    logger.info("migration 081: session_id unique index ensured")
