"""Schema migration for ``restart_intents`` — widen CHECK and add ``kill_boundary_at``."""

from __future__ import annotations

import sqlite3

from .restart_intent_states import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
    STATUS_VERIFYING_ACTIVATION,
)

_DDL = """
CREATE TABLE IF NOT EXISTS restart_intents (
    intent_id            TEXT PRIMARY KEY,
    service              TEXT NOT NULL,
    action               TEXT NOT NULL DEFAULT 'restart',
    status               TEXT NOT NULL CHECK (status IN (
        'pending_drain','drained_restarting','verifying_activation',
        'completed','activation_unverified',
        'failed','timeout','force_requested','cancelled')),
    drain_epoch          INTEGER,
    worker_id            TEXT,
    worker_started_at    TEXT,
    deadline_at          TEXT,
    last_seen_event_seq  INTEGER NOT NULL DEFAULT 0,
    reason               TEXT,
    kill_boundary_at     TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_live
    ON restart_intents(service)
    WHERE status IN ('pending_drain','drained_restarting');
"""


def apply_restart_intent_schema(conn: sqlite3.Connection) -> None:
    """Ensure restart-intent table matches the current CHECK and columns."""
    conn.executescript(_DDL)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
    ).fetchone()
    if row is None or row[0] is None:
        return
    sql = str(row[0])
    if "'verifying_activation'" in sql and "kill_boundary_at" in sql:
        return
    conn.executescript(
        f"""
        CREATE TABLE restart_intents_v2 (
            intent_id            TEXT PRIMARY KEY,
            service              TEXT NOT NULL,
            action               TEXT NOT NULL DEFAULT 'restart',
            status               TEXT NOT NULL CHECK (status IN (
                '{STATUS_PENDING_DRAIN}','{STATUS_DRAINED_RESTARTING}',
                '{STATUS_VERIFYING_ACTIVATION}',
                '{STATUS_COMPLETED}','{STATUS_ACTIVATION_UNVERIFIED}',
                '{STATUS_FAILED}','{STATUS_TIMEOUT}',
                '{STATUS_FORCE_REQUESTED}','{STATUS_CANCELLED}')),
            drain_epoch          INTEGER,
            worker_id            TEXT,
            worker_started_at    TEXT,
            deadline_at          TEXT,
            last_seen_event_seq  INTEGER NOT NULL DEFAULT 0,
            reason               TEXT,
            kill_boundary_at     TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        );
        INSERT INTO restart_intents_v2 (
            intent_id, service, action, status, drain_epoch, worker_id,
            worker_started_at, deadline_at, last_seen_event_seq, reason,
            kill_boundary_at, created_at, updated_at
        )
        SELECT
            intent_id, service, action, status, drain_epoch, worker_id,
            worker_started_at, deadline_at, last_seen_event_seq, reason,
            NULL, created_at, updated_at
        FROM restart_intents;
        DROP TABLE restart_intents;
        ALTER TABLE restart_intents_v2 RENAME TO restart_intents;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_live
            ON restart_intents(service)
            WHERE status IN ('pending_drain','drained_restarting');
        """
    )


__all__ = ["_DDL", "apply_restart_intent_schema"]
