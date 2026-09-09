"""migration_010: Append-only resume fence journal.

``resume_fence_events`` folds to ``{armed, poured, released, expired}`` per
``fence_id``. Hooks and send-gate read the fold; they never co-mutate rows.
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_010"


def run(conn: sqlite3.Connection) -> None:
    """Create ``resume_fence_events`` and indexes for fold lookups."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS resume_fence_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fence_id        TEXT NOT NULL,
            root_thread     TEXT NOT NULL,
            transcript_id   TEXT,
            event           TEXT NOT NULL,
            payload_json    TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_resume_fence_events_fence_id_desc
            ON resume_fence_events(fence_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_resume_fence_events_root_transcript_desc
            ON resume_fence_events(root_thread, transcript_id, id DESC);
        """
    )
