"""Initial triggers table for trigger-schedule.sqlite."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "001_triggers"

_DDL = """
CREATE TABLE IF NOT EXISTS triggers (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    fire_at             TEXT NOT NULL,
    prompt_uri          TEXT NOT NULL,
    purpose             TEXT NOT NULL DEFAULT 'operator-proxy',
    model               TEXT NOT NULL DEFAULT 'opus-5',
    arc                 TEXT,
    so_what             TEXT,
    status              TEXT NOT NULL CHECK (
        status IN ('scheduled','firing','fired','failed','cancelled')
    ),
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    last_error          TEXT,
    claimed_at          TEXT,
    execution_id        TEXT,
    fired_at            TEXT,
    terminal_status     TEXT,
    archive_uri         TEXT,
    cancelled_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_triggers_status_fire_at
    ON triggers(status, fire_at);
CREATE INDEX IF NOT EXISTS idx_triggers_fired_reconcile
    ON triggers(status) WHERE status = 'fired' AND terminal_status IS NULL;
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
