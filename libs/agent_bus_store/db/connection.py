"""Database connection, schema initialization, and shared helpers."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

_DEFAULT_DB_PATH = "/data/messages.db"


def _db_path() -> str:
    """Resolve DB path at call time so env overrides apply after import."""
    return os.environ.get("AGENT_BUS_DB_PATH", _DEFAULT_DB_PATH)


_MESSAGES_SCHEMA = """\
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,
    thread      TEXT NOT NULL,
    body        TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    read        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, read);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread);
"""

_TURNS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS threads (
    id         TEXT PRIMARY KEY,
    slug       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'blocked', 'waiting', 'closed')),
    summary    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);

CREATE TABLE IF NOT EXISTS turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread          TEXT NOT NULL REFERENCES threads(id),
    turn_number     INTEGER NOT NULL,
    from_agent      TEXT NOT NULL,
    to_agent        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'resolved', 'superseded', 'waiting')),
    supersedes_turn INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    read_at         TEXT,
    UNIQUE(thread, turn_number)
);
CREATE INDEX IF NOT EXISTS idx_turns_thread ON turns(thread, turn_number DESC);
CREATE INDEX IF NOT EXISTS idx_turns_to_unread ON turns(to_agent, read_at)
    WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(thread, status);

CREATE TABLE IF NOT EXISTS thread_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _migrate_turns_drop_agent_checks(conn: sqlite3.Connection) -> None:
    """Remove hardcoded CHECK constraints on from_agent/to_agent.

    Agent name validation is handled by Pydantic at the API layer.
    Hardcoded CHECK constraints in SQLite create drift when new agents
    are added to the Python enum.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='turns'"
    ).fetchone()
    if row is None:
        return
    schema_sql: str = row[0]
    if "CHECK (from_agent" not in schema_sql:
        return
    conn.executescript("""
        CREATE TABLE turns_new (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            thread          TEXT NOT NULL REFERENCES threads(id),
            turn_number     INTEGER NOT NULL,
            from_agent      TEXT NOT NULL,
            to_agent        TEXT NOT NULL,
            subject         TEXT NOT NULL,
            body            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'resolved', 'superseded', 'waiting')),
            supersedes_turn INTEGER,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            read_at         TEXT,
            UNIQUE(thread, turn_number)
        );
        INSERT INTO turns_new SELECT * FROM turns;
        DROP TABLE turns;
        ALTER TABLE turns_new RENAME TO turns;
    """)


_TURNS_INDEXES = """\
CREATE INDEX IF NOT EXISTS idx_turns_thread ON turns(thread, turn_number DESC);
CREATE INDEX IF NOT EXISTS idx_turns_to_unread ON turns(to_agent, read_at)
    WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(thread, status);
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_MESSAGES_SCHEMA)
        conn.executescript(_TURNS_SCHEMA)
        _migrate_turns_drop_agent_checks(conn)
        conn.executescript(_TURNS_INDEXES)


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
