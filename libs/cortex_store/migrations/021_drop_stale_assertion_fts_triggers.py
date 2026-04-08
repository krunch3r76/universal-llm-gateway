"""Migration 021: Remove stale assertion FTS content-sync triggers.

Migration 020 replaced the old content-synced ``assertions_fts`` table with a
manually maintained v3 table (``indexed_text``, ``assertion_id``, ``entity_id``).
Databases that had already applied migration 020 could still retain the old
content-sync triggers from migration 015, causing ``POST /assertions`` to fail
with:

    sqlite3.OperationalError: table assertions_fts has no column named claim

This cleanup migration removes the obsolete triggers for existing DBs. The v3
index is maintained explicitly in application code, so no replacement triggers
are required.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("cortex-api.migration.021")


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TRIGGER IF EXISTS assertions_fts_insert")
    conn.execute("DROP TRIGGER IF EXISTS assertions_fts_supersede")
    logger.info("Dropped stale assertions_fts content-sync triggers")
