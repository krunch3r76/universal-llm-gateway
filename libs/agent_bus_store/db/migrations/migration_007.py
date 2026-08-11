"""migration_007: Append-only lane↔branch associations (arc 6655).

Creates ``thread_branch_associations`` with exactly three columns. Current branch
per lane is derived via ``MAX(id)`` — no mutable current pointer or clock column.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_007"


def run(conn: sqlite3.Connection) -> None:
    """Create ``thread_branch_associations`` and descending id index per lane."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS thread_branch_associations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            branch_name TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_thread_branch_assoc_thread_id_desc
            ON thread_branch_associations(thread_id, id DESC);
        """
    )
