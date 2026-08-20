"""migration_009: Append-only CSE session-address associations.

Creates ``thread_cse_associations`` with folded current via ``MAX(id)``.
Identity is ``cse_chat_url``; ``cse_registration_id`` is last-known attach.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_009"


def run(conn: sqlite3.Connection) -> None:
    """Create ``thread_cse_associations`` and descending id index per thread."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS thread_cse_associations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id            TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            cse_chat_url         TEXT NOT NULL,
            cse_registration_id  TEXT,
            bound_by             TEXT,
            evidence             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_thread_cse_assoc_thread_id_desc
            ON thread_cse_associations(thread_id, id DESC);
        """
    )
