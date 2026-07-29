"""Add predicate columns and drop status CHECK (application-enforced domain)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "002_predicates"

# Statement-by-statement (not executescript): Python's executescript issues an
# implicit COMMIT before running, defeating A3's one-transaction rebuild.
_STATEMENTS = (
    """
    CREATE TABLE triggers_new (
        id                  TEXT PRIMARY KEY,
        created_at          TEXT NOT NULL,
        created_by          TEXT NOT NULL,
        fire_at             TEXT NOT NULL,
        prompt_uri          TEXT NOT NULL,
        purpose             TEXT NOT NULL DEFAULT 'operator-proxy',
        model               TEXT NOT NULL DEFAULT 'opus-5',
        arc                 TEXT,
        so_what             TEXT,
        status              TEXT NOT NULL,
        attempts            INTEGER NOT NULL DEFAULT 0,
        max_attempts        INTEGER NOT NULL DEFAULT 3,
        last_error          TEXT,
        claimed_at          TEXT,
        execution_id        TEXT,
        fired_at            TEXT,
        terminal_status     TEXT,
        archive_uri         TEXT,
        cancelled_at        TEXT,
        predicate           TEXT,
        predicate_args      TEXT,
        expires_at          TEXT,
        last_predicate_error TEXT
    )
    """,
    """
    INSERT INTO triggers_new (
        id, created_at, created_by, fire_at, prompt_uri,
        purpose, model, arc, so_what, status,
        attempts, max_attempts, last_error, claimed_at,
        execution_id, fired_at, terminal_status, archive_uri, cancelled_at,
        predicate, predicate_args, expires_at, last_predicate_error
    )
    SELECT
        id, created_at, created_by, fire_at, prompt_uri,
        purpose, model, arc, so_what, status,
        attempts, max_attempts, last_error, claimed_at,
        execution_id, fired_at, terminal_status, archive_uri, cancelled_at,
        NULL, NULL, NULL, NULL
    FROM triggers
    """,
    "DROP TABLE triggers",
    "ALTER TABLE triggers_new RENAME TO triggers",
    """
    CREATE INDEX IF NOT EXISTS idx_triggers_status_fire_at
        ON triggers(status, fire_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_triggers_fired_reconcile
        ON triggers(status) WHERE status = 'fired' AND terminal_status IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_triggers_expire_scan
        ON triggers(status, expires_at)
        WHERE status = 'scheduled' AND expires_at IS NOT NULL
    """,
)


def migrate(conn: sqlite3.Connection) -> None:
    """Rebuild triggers with predicate columns in one SQLite transaction.

    Leads with ``DROP TABLE IF EXISTS triggers_new`` so a prior non-atomic
    partial apply cannot wedge on ``triggers_new already exists``.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DROP TABLE IF EXISTS triggers_new")
        for statement in _STATEMENTS:
            conn.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
