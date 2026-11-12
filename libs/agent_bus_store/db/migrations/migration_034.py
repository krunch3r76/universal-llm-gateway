"""migration_034: hop-before-checkpoint precedence projection (friction:43744 S2)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_034"


def run(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hop_precedence_state (
            thread_id                 TEXT    NOT NULL,
            work_key                  TEXT,
            hop_turn_id               INTEGER NOT NULL,
            required_checkpoint_uri   TEXT,
            required_read_sha256      TEXT,
            hop_status                TEXT    NOT NULL,
            held_reason               TEXT,
            checkpoint_turn_id        INTEGER,
            checkpoint_read_sha256    TEXT,
            updated_at                TEXT    NOT NULL,
            PRIMARY KEY (thread_id, hop_turn_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hop_precedence_work_key "
        "ON hop_precedence_state(work_key)"
    )
