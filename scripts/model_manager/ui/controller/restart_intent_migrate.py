"""Schema migration for ``restart_intents`` — widen CHECK and add ``kill_boundary_at``."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

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

# Abandoned arm bound. A live supervisor's 30s progress tick does not refresh
# this; a joining create_intent does. In-flight rows receive a fresh window
# at migration (from now), not from created_at.
INTENT_EXPIRY_WINDOW_S = 600.0

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
    caller_agent         TEXT,
    armed_at             TEXT,
    expires_at           TEXT,
    park_live            INTEGER NOT NULL DEFAULT 0,
    park_summary         TEXT,
    wait_for_boundary    INTEGER NOT NULL DEFAULT 0,
    status_reason        TEXT,
    status_changed_at    TEXT,
    transitions          TEXT NOT NULL DEFAULT '[]',
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
    _ensure_park_columns(conn)
    _ensure_wait_for_boundary_column(conn)
    _ensure_arm_columns(conn)
    _ensure_transition_columns(conn)
    _ensure_kill_cas_indexes(conn)


def _ensure_arm_columns(conn: sqlite3.Connection) -> None:
    """Add arm columns. Legacy null windows get a fresh bound only once.

    ``wait_for_boundary`` arms may keep ``expires_at`` NULL (no hard expiry).
    Backfill runs only when ``expires_at`` is first introduced — not on every
    schema apply — so intentional nulls survive store reopen (a:37197).
    """
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
    }
    if "caller_agent" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN caller_agent TEXT")
    if "armed_at" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN armed_at TEXT")
    added_expires = "expires_at" not in cols
    if added_expires:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN expires_at TEXT")
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=INTENT_EXPIRY_WINDOW_S)).isoformat()
        armed = now.isoformat()
        conn.execute(
            "UPDATE restart_intents SET armed_at=?, expires_at=? "
            "WHERE expires_at IS NULL "
            "AND COALESCE(wait_for_boundary, 0) = 0 "
            "AND status IN ('pending_drain', 'drained_restarting')",
            (armed, expires),
        )


def _ensure_park_columns(conn: sqlite3.Connection) -> None:
    """Steer-restart v1: park_live intent flag + last sweep summary JSON."""
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
    }
    if "park_live" not in cols:
        conn.execute(
            "ALTER TABLE restart_intents ADD COLUMN park_live INTEGER NOT NULL DEFAULT 0"
        )
    if "park_summary" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN park_summary TEXT")


def _ensure_transition_columns(conn: sqlite3.Connection) -> None:
    """Status-transition columns. Legacy rows keep an empty history."""
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
    }
    if "status_reason" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN status_reason TEXT")
    if "status_changed_at" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN status_changed_at TEXT")
    if "transitions" not in cols:
        conn.execute(
            "ALTER TABLE restart_intents ADD COLUMN transitions TEXT NOT NULL DEFAULT '[]'"
        )


def _ensure_wait_for_boundary_column(conn: sqlite3.Connection) -> None:
    """a:37197 — defer begin_drain until GIW idle; optional unbounded arm."""
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
    }
    if "wait_for_boundary" not in cols:
        conn.execute(
            "ALTER TABLE restart_intents ADD COLUMN wait_for_boundary "
            "INTEGER NOT NULL DEFAULT 0"
        )


__all__ = ["INTENT_EXPIRY_WINDOW_S", "_DDL", "apply_restart_intent_schema"]
