"""DDL for mechanical-land run store (spec §4.1–4.2) on the dispatch ledger sqlite."""

from __future__ import annotations

import sqlite3

_MIGRATION_KEY = "mechanical_land_v1"

_DDL = """
CREATE TABLE IF NOT EXISTS mechanical_land_runs (
    run_id              TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,
    source_ref          TEXT NOT NULL,
    work_key            TEXT NOT NULL,
    caller_agent        TEXT NOT NULL,
    dispatch_thread_id  TEXT NOT NULL,
    lane_thread_id      TEXT,
    policy_json         TEXT NOT NULL,
    policy_sha256       TEXT NOT NULL,
    status              TEXT NOT NULL,
    current_step        TEXT,
    current_attempt     INTEGER,
    flow                TEXT NOT NULL DEFAULT 'main',
    result_json         TEXT,
    idempotency_key     TEXT UNIQUE,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mechanical_land_runs_status
    ON mechanical_land_runs(status);
CREATE TABLE IF NOT EXISTS mechanical_land_journal (
    run_id          TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    prev_seq        INTEGER NOT NULL,
    step_id         TEXT NOT NULL,
    attempt         INTEGER NOT NULL,
    flow            TEXT NOT NULL DEFAULT 'main',
    event           TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    source          TEXT NOT NULL,
    written_at      TEXT NOT NULL,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES mechanical_land_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_mechanical_land_journal_run
    ON mechanical_land_journal(run_id, seq DESC);
CREATE TABLE IF NOT EXISTS mechanical_land_schema_meta (
    key     TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


def ensure_mechanical_land_schema(conn: sqlite3.Connection) -> None:
    """Apply mechanical-land tables once per database."""
    try:
        row = conn.execute(
            "SELECT 1 FROM mechanical_land_schema_meta WHERE key=?",
            (_MIGRATION_KEY,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        return
    conn.executescript(_DDL)
    from datetime import UTC, datetime

    conn.execute(
        "INSERT INTO mechanical_land_schema_meta(key, applied_at) VALUES (?, ?)",
        (_MIGRATION_KEY, datetime.now(UTC).isoformat()),
    )
