"""Migration 044: Add ``handoff_prompt`` column to ``session_journals``.

Closes the cortex-api storage gap from ``decision:rj-handoff-kind-retirement``
(operator DECIDED 2026-05-27, agent-bus thread 1107): forward-state-of-task
is not a reflective journal concern; it belongs in session continuity.

After this column lands, ``close_session`` persists ``handoff_prompt`` on
the journal row instead of writing a ``reflective_journal`` row with
``kind="handoff"`` plus a ``journal_links`` row of ``link_type="handoff_for"``.
Existing legacy rows in those tables remain queryable; the new write
path stops touching them.

SQLite does not support ``IF NOT EXISTS`` on ``ALTER TABLE ADD COLUMN``,
so the migration introspects ``PRAGMA table_info(session_journals)``
before issuing the DDL. Re-application on a partial-state database is
a no-op rather than ``OperationalError: duplicate column name``.

Column shape: ``handoff_prompt TEXT`` (nullable, default NULL). NULL is
the natural "no handoff supplied" sentinel; matches the existing
``SessionCloseRequest.handoff_prompt: str | None`` optional semantic.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.044")


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE ADD COLUMN — checks schema before issuing DDL."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(session_journals)")}
    if "handoff_prompt" in cols:
        logger.info(
            "migration 044: session_journals.handoff_prompt already present; no-op"
        )
        return
    conn.execute("ALTER TABLE session_journals ADD COLUMN handoff_prompt TEXT")
    logger.info("migration 044: session_journals.handoff_prompt added (nullable TEXT)")
