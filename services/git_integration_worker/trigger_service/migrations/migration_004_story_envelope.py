"""Add story envelope columns to triggers table."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "004_story_envelope"

_DDL = """
ALTER TABLE triggers ADD COLUMN story_id TEXT;
ALTER TABLE triggers ADD COLUMN story_id_source TEXT;
CREATE INDEX IF NOT EXISTS idx_triggers_story_id ON triggers(story_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
