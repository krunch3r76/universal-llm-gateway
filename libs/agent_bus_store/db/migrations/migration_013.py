"""migration_013: resume_fence_bundles for GET delivery after pour."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_013"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS resume_fence_bundles (
            fence_id    TEXT PRIMARY KEY,
            sha256      TEXT NOT NULL,
            body_json   TEXT NOT NULL,
            tip_turn_id INTEGER NOT NULL,
            card_sha256 TEXT,
            head_sha    TEXT,
            built_at    TEXT NOT NULL
        )
        """
    )
