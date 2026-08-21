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
CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_pending
    ON restart_intents(service)
    WHERE status = 'pending_drain';
CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_kill
    ON restart_intents(worker_id, worker_started_at, drain_epoch)
    WHERE status = 'drained_restarting'
      AND worker_id IS NOT NULL
      AND worker_started_at IS NOT NULL
      AND drain_epoch IS NOT NULL;
"""


def _ensure_kill_cas_indexes(conn: sqlite3.Connection) -> None:
    """R3′: pending coalesces per service; kill-commit is generation-scoped.

    Replaces ``idx_restart_intent_live`` (one live row per service across
    pending_drain ∪ drained_restarting) so a timeout-terminal incumbent can
    keep-await while a second pending_drain is admitted, and two supervisors
    cannot both commit kill on the same generation.
    """
    conn.execute("DROP INDEX IF EXISTS idx_restart_intent_live")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_pending "
        "ON restart_intents(service) WHERE status = 'pending_drain'"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_kill "
        "ON restart_intents(worker_id, worker_started_at, drain_epoch) "
        "WHERE status = 'drained_restarting' "
        "AND worker_id IS NOT NULL AND worker_started_at IS NOT NULL "
        "AND drain_epoch IS NOT NULL"
    )


def apply_restart_intent_schema(conn: sqlite3.Connection) -> None:
    """Ensure restart-intent table matches the current CHECK and columns."""
    conn.executescript(_DDL)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
    ).fetchone()
    if row is None or row[0] is None:
        _ensure_kill_cas_indexes(conn)
        return
    sql = str(row[0])
    if "'verifying_activation'" not in sql or "kill_boundary_at" not in sql:
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
            """
        )
    _ensure_kill_cas_indexes(conn)


__all__ = ["_DDL", "apply_restart_intent_schema"]
