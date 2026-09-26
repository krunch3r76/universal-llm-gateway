"""Database connection, schema initialization, and shared helpers."""

from __future__ import annotations

import os
import sqlite3
import threading
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

CREATE TABLE IF NOT EXISTS turn_attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id    INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    path       TEXT NOT NULL,
    mime_type  TEXT,
    size_bytes INTEGER,
    sha256     TEXT
);
CREATE INDEX IF NOT EXISTS idx_attachments_turn ON turn_attachments(turn_id);

CREATE TABLE IF NOT EXISTS thread_tags (
    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    tag       TEXT NOT NULL,
    PRIMARY KEY (thread_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_thread_tags_tag ON thread_tags(tag);
"""


def init_db() -> None:
    """Create base schema and run migrations at process start (not a request writer)."""
    from .migrations import run_migrations

    with connect() as conn:
        conn.executescript(_MESSAGES_SCHEMA)
        conn.executescript(_TURNS_SCHEMA)
        run_migrations(conn)


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


def _write_busy_timeout_ms() -> int | None:
    """Positive ``PRAGMA busy_timeout`` from env at call time, or None when off."""
    raw = os.environ.get("AGENT_BUS_WRITE_BUSY_TIMEOUT_MS")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = int(str(raw).strip())
    except ValueError:
        return None
    if val <= 0:
        return None
    return val


@contextmanager
def write_connect() -> Generator[sqlite3.Connection, None, None]:
    """Serialize writers via an in-process FIFO ticket; readers use ``connect()``."""
    from . import write_ticket as wt

    if wt.outer_depth() > 0:
        wt.enter_nested()
        conn = wt.outer_connection()
        assert isinstance(conn, sqlite3.Connection)
        try:
            yield conn
        finally:
            wt.leave_nested()
        return

    ticket_event: threading.Event | None = None
    wait_finished = False
    conn: sqlite3.Connection | None = None
    try:
        ticket_event = wt.enqueue_and_wait()
        wait_finished = True
        conn = sqlite3.connect(_db_path(), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        busy_ms = _write_busy_timeout_ms()
        if busy_ms is not None:
            conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        conn.execute("BEGIN IMMEDIATE")
        wt.set_outer_connection(conn)
        state_depth = wt.outer_depth()
        wt.enter_nested()
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            if wt.outer_depth() > state_depth:
                wt.leave_nested()
    except BaseException:
        if ticket_event is not None and not wait_finished:
            wt.cancel_wait(ticket_event)
        raise
    finally:
        if conn is not None:
            conn.close()
            wt.clear_outer_connection()
        if ticket_event is not None and wait_finished:
            wt.release_ticket(ticket_event)


def now() -> str:
    """Return the current UTC timestamp in agent-bus wire format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
