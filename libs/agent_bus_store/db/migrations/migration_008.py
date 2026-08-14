"""migration_008: Append-only lane parentage associations.

Creates ``thread_lane_associations`` with folded current via ``MAX(id)``.
Child lane delete cascades; parent delete is restricted when children exist.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_008"


def run(conn: sqlite3.Connection) -> None:
    """Create ``thread_lane_associations`` and descending id index per lane."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS thread_lane_associations (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id          TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
            parent_thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE RESTRICT,
            lane_role          TEXT NOT NULL,
            bound_by           TEXT,
            evidence           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_thread_lane_assoc_thread_id_desc
            ON thread_lane_associations(thread_id, id DESC);
        """
    )
