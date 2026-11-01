"""Schema migration for ``restart_intents`` — widen CHECK and add ``kill_boundary_at``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from .restart_intent_states import (
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_EXPIRED,
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
        'failed','timeout','force_requested','cancelled','expired')),
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


_OWED_WAKE_DDL = """
CREATE TABLE IF NOT EXISTS owed_wake (
    row_id              TEXT PRIMARY KEY,
    holder_id           TEXT NOT NULL,
    producer            TEXT NOT NULL CHECK (producer IN ('milestone', 'drain_complete')),
    lane_thread_id      TEXT,
    turn_refs           TEXT NOT NULL DEFAULT '[]',
    arming_lanes        TEXT NOT NULL DEFAULT '[]',
    restart_intent_id   TEXT,
    dispatched_at       TEXT,
    closed_at           TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_owed_wake_open_holder
    ON owed_wake(holder_id)
    WHERE closed_at IS NULL;
"""


_V2_MIGRATION_TEMP = "restart_intents_v2"


def _recover_interrupted_rebuilds(conn: sqlite3.Connection) -> None:
    """Finish or discard a crashed table rebuild BEFORE ``_DDL`` runs (41247).

    ``_DDL`` uses ``CREATE TABLE IF NOT EXISTS``: if a rebuild crashed after
    ``DROP TABLE restart_intents`` but before the temp RENAME, running ``_DDL``
    first would mint an empty up-to-date main and a later step would drop the
    temp still holding every row. Recover here so the rows win.

    * main missing, temp present -> rename temp back (expired_v1 preferred:
      it is the later stage and already holds the widest shape).
    * main present, temp present -> crash before DROP; main is authoritative,
      discard the partial temp.
    """
    with _expired_status_migration_txn(conn):
        has_main = _table_exists(conn, "restart_intents")
        temps = [
            t
            for t in (_EXPIRED_MIGRATION_TEMP, _V2_MIGRATION_TEMP)
            if _table_exists(conn, t)
        ]
        if not temps:
            return
        if not has_main:
            conn.execute(f"ALTER TABLE {temps[0]} RENAME TO restart_intents")
            temps = temps[1:]
        for t in temps:
            conn.execute(f"DROP TABLE IF EXISTS {t}")


def _ensure_v2_shape(conn: sqlite3.Connection) -> None:
    """Widen legacy CHECK / add ``kill_boundary_at`` in one atomic txn."""
    with _expired_status_migration_txn(conn):
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
        ).fetchone()
        if row is None or row[0] is None:
            return
        sql = str(row[0])
        if "'verifying_activation'" in sql and "kill_boundary_at" in sql:
            return
        conn.execute(f"DROP TABLE IF EXISTS {_V2_MIGRATION_TEMP}")
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_V2_MIGRATION_TEMP} (
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
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO {_V2_MIGRATION_TEMP} (
                intent_id, service, action, status, drain_epoch, worker_id,
                worker_started_at, deadline_at, last_seen_event_seq, reason,
                kill_boundary_at, created_at, updated_at
            )
            SELECT
                intent_id, service, action, status, drain_epoch, worker_id,
                worker_started_at, deadline_at, last_seen_event_seq, reason,
                NULL, created_at, updated_at
            FROM restart_intents
            """
        )
        conn.execute("DROP TABLE restart_intents")
        conn.execute(f"ALTER TABLE {_V2_MIGRATION_TEMP} RENAME TO restart_intents")


def apply_restart_intent_schema(conn: sqlite3.Connection) -> None:
    """Ensure restart-intent table matches the current CHECK and columns."""
    _recover_interrupted_rebuilds(conn)
    conn.executescript(_DDL)
    conn.executescript(_OWED_WAKE_DDL)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
    ).fetchone()
    if row is None or row[0] is None:
        _ensure_kill_cas_indexes(conn)
        return
    _ensure_v2_shape(conn)
    _ensure_park_columns(conn)
    _ensure_wait_for_boundary_column(conn)
    _ensure_arm_columns(conn)
    _ensure_transition_columns(conn)
    _ensure_kill_cas_indexes(conn)
    _ensure_arming_lanes_column(conn)
    _ensure_owed_wake_attempt_columns(conn)
    _ensure_expired_status(conn)


_EXPIRED_MIGRATION_TEMP = "restart_intents_expired_v1"

_EXPIRED_MIGRATION_TARGET_COLS = (
    "intent_id",
    "service",
    "action",
    "status",
    "drain_epoch",
    "worker_id",
    "worker_started_at",
    "deadline_at",
    "last_seen_event_seq",
    "reason",
    "kill_boundary_at",
    "caller_agent",
    "armed_at",
    "expires_at",
    "park_live",
    "park_summary",
    "wait_for_boundary",
    "status_reason",
    "status_changed_at",
    "transitions",
    "arming_lanes",
    "caller_thread_id",
    "created_at",
    "updated_at",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _restart_intents_allows_expired(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='restart_intents'"
    ).fetchone()
    if row is None or row[0] is None:
        return False
    return f"'{STATUS_EXPIRED}'" in str(row[0])


def _create_expired_migration_temp(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE {_EXPIRED_MIGRATION_TEMP} (
            intent_id            TEXT PRIMARY KEY,
            service              TEXT NOT NULL,
            action               TEXT NOT NULL DEFAULT 'restart',
            status               TEXT NOT NULL CHECK (status IN (
                '{STATUS_PENDING_DRAIN}','{STATUS_DRAINED_RESTARTING}',
                '{STATUS_VERIFYING_ACTIVATION}',
                '{STATUS_COMPLETED}','{STATUS_ACTIVATION_UNVERIFIED}',
                '{STATUS_FAILED}','{STATUS_TIMEOUT}',
                '{STATUS_FORCE_REQUESTED}','{STATUS_CANCELLED}',
                '{STATUS_EXPIRED}')),
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
            arming_lanes         TEXT,
            caller_thread_id     TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        )
        """
    )


@contextmanager
def _expired_status_migration_txn(conn: sqlite3.Connection) -> Iterator[None]:
    """One explicit txn for the expired CHECK rebuild (IMMEDIATE when alone)."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT restart_intents_expired_migration")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT restart_intents_expired_migration")
            raise
        finally:
            conn.execute("RELEASE SAVEPOINT restart_intents_expired_migration")
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def _ensure_expired_status(conn: sqlite3.Connection) -> None:
    """Widen CHECK to allow terminal ``expired`` (friction:41247).

    Idempotent and atomic: ``executescript`` implicit commits left
    ``restart_intents_expired_v1`` behind on crash (41247). A crash between
    DROP and RENAME is recovered by ``_recover_interrupted_rebuilds`` before
    ``_DDL`` can mint an empty main.
    """
    with _expired_status_migration_txn(conn):
        has_main = _table_exists(conn, "restart_intents")
        has_temp = _table_exists(conn, _EXPIRED_MIGRATION_TEMP)

        if has_main and _restart_intents_allows_expired(conn):
            if has_temp:
                conn.execute(f"DROP TABLE IF EXISTS {_EXPIRED_MIGRATION_TEMP}")
            return

        if not has_main and has_temp:
            conn.execute(
                f"ALTER TABLE {_EXPIRED_MIGRATION_TEMP} RENAME TO restart_intents"
            )
            _ensure_kill_cas_indexes(conn)
            return

        if not has_main:
            return

        conn.execute(f"DROP TABLE IF EXISTS {_EXPIRED_MIGRATION_TEMP}")
        old_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
        }
        shared = [c for c in _EXPIRED_MIGRATION_TARGET_COLS if c in old_cols]
        col_csv = ", ".join(shared)
        _create_expired_migration_temp(conn)
        conn.execute(
            f"INSERT INTO {_EXPIRED_MIGRATION_TEMP} ({col_csv}) "
            f"SELECT {col_csv} FROM restart_intents"
        )
        conn.execute("DROP TABLE restart_intents")
        conn.execute(
            f"ALTER TABLE {_EXPIRED_MIGRATION_TEMP} RENAME TO restart_intents"
        )
        _ensure_kill_cas_indexes(conn)


def _ensure_arming_lanes_column(conn: sqlite3.Connection) -> None:
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(restart_intents)").fetchall()
    }
    if "arming_lanes" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN arming_lanes TEXT")
    if "caller_thread_id" not in cols:
        conn.execute("ALTER TABLE restart_intents ADD COLUMN caller_thread_id TEXT")


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


def _ensure_owed_wake_attempt_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(owed_wake)").fetchall()}
    if "attempt_count" not in cols:
        conn.execute(
            "ALTER TABLE owed_wake ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
        )
    if "next_attempt_at" not in cols:
        conn.execute("ALTER TABLE owed_wake ADD COLUMN next_attempt_at TEXT")
    if "dispatch_outcome" not in cols:
        conn.execute("ALTER TABLE owed_wake ADD COLUMN dispatch_outcome TEXT")


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
