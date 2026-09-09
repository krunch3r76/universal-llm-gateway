"""Migration 080: ``session_lids`` / ``session_windows`` facet views (v2 R-V)."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.080")

_VIEW_LIDS = """
CREATE VIEW IF NOT EXISTS session_lids AS
  SELECT * FROM session_journals
  WHERE closed_by IS NULL OR closed_by != 'succession';
"""

_VIEW_WINDOWS = """
CREATE VIEW IF NOT EXISTS session_windows AS
  SELECT * FROM session_journals
  WHERE conversation_uuid IS NOT NULL OR verbatim_sha256 IS NOT NULL;
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent CREATE VIEW for lid/window facets."""
    conn.executescript(_VIEW_LIDS)
    conn.executescript(_VIEW_WINDOWS)
    logger.info("migration 080: session_lids + session_windows views ensured")
