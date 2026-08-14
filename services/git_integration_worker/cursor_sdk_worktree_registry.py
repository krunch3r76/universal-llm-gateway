"""SQLite registry and mint mutex for lane-owned Lane-B worktrees."""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.git_integration_worker.cursor_dispatch_ledger import _connect

_MINT_MUTEX_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_mint_mutex (
    mutex_key     TEXT PRIMARY KEY,
    holder_id     TEXT NOT NULL,
    acquired_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_worktrees (
    thread_id             TEXT PRIMARY KEY,
    worktree_path         TEXT NOT NULL,
    branch_name           TEXT NOT NULL,
    branch_point          TEXT NOT NULL,
    minted_at             TEXT NOT NULL,
    last_dispatch_id      TEXT,
    salvage_refusal_count INTEGER NOT NULL DEFAULT 0,
    quarantined_at        TEXT
);
CREATE TABLE IF NOT EXISTS cursor_sdk_dispatch_worktrees (
    dispatch_id   TEXT PRIMARY KEY,
    worktree_path TEXT NOT NULL,
    branch_name   TEXT NOT NULL,
    branch_point  TEXT NOT NULL,
    minted_at     TEXT NOT NULL
);
"""

_QUARANTINE_AFTER = 3
_LANE_COLUMN_MIGRATIONS = (
    ("salvage_refusal_count", "INTEGER NOT NULL DEFAULT 0"),
    ("quarantined_at", "TEXT"),
    ("last_dispatch_id", "TEXT"),
)

_MINT_LOCK_POLL_S = 0.02
_MINT_LOCK_TIMEOUT_S = 120.0


@dataclass(frozen=True, slots=True)
class DispatchWorktreeRecord:
    """Registered Lane-B worktree metadata from the lane registry."""

    worktree_path: Path
    branch_name: str
    branch_point: str
    thread_id: str = ""
    last_dispatch_id: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_worktree_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MINT_MUTEX_DDL)
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(cursor_sdk_lane_worktrees)")
    }
    for name, decl in _LANE_COLUMN_MIGRATIONS:
        if name not in cols:
            try:
                conn.execute(
                    f"ALTER TABLE cursor_sdk_lane_worktrees ADD COLUMN {name} {decl}"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise


def master_mint_mutex_key(source_repo: Path) -> str:
    """Master-keyed mutex identity for serialized ``git worktree add``."""
    return str(source_repo.resolve())


def _try_acquire_mint_mutex(*, mutex_key: str, holder_id: str) -> bool:
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT holder_id FROM cursor_sdk_mint_mutex WHERE mutex_key=?",
            (mutex_key,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO cursor_sdk_mint_mutex (mutex_key, holder_id, acquired_at) "
                "VALUES (?, ?, ?)",
                (mutex_key, holder_id, _now()),
            )
            return True
        return row["holder_id"] == holder_id


def release_mint_mutex(*, mutex_key: str, holder_id: str) -> None:
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_mint_mutex WHERE mutex_key=? AND holder_id=?",
            (mutex_key, holder_id),
        )


def acquire_mint_mutex_blocking(
    *,
    source_repo: Path,
    holder_id: str,
    timeout_s: float = _MINT_LOCK_TIMEOUT_S,
) -> str:
    """Block until the master mint mutex is held; return mutex key."""
    mutex_key = master_mint_mutex_key(source_repo)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if _try_acquire_mint_mutex(mutex_key=mutex_key, holder_id=holder_id):
                return mutex_key
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"mint mutex unavailable for {mutex_key!r} after {timeout_s:.0f}s"
            )
        time.sleep(_MINT_LOCK_POLL_S)


def _row_to_record(row: sqlite3.Row) -> DispatchWorktreeRecord:
    return DispatchWorktreeRecord(
        worktree_path=Path(row["worktree_path"]),
        branch_name=row["branch_name"],
        branch_point=row["branch_point"],
        thread_id=str(row["thread_id"] or ""),
        last_dispatch_id=row["last_dispatch_id"],
    )


def register_lane_worktree(
    *,
    thread_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    last_dispatch_id: str | None = None,
) -> None:
    """Insert or replace the lane-owned worktree row."""
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
    )

    clear_disposition(branch_name=branch_name)
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_lane_worktrees "
            "(thread_id, worktree_path, branch_name, branch_point, minted_at, "
            "last_dispatch_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                str(worktree_path.resolve()),
                branch_name,
                branch_point,
                _now(),
                last_dispatch_id,
            ),
        )


def touch_lane_worktree_dispatch(*, thread_id: str, dispatch_id: str) -> None:
    """Record the latest dispatch occupying an existing lane worktree."""
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET last_dispatch_id=? WHERE thread_id=?",
            (dispatch_id, thread_id),
        )


def unregister_lane_worktree(*, thread_id: str) -> None:
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_lane_worktrees WHERE thread_id=?",
            (thread_id,),
        )


def lookup_lane_worktree(*, thread_id: str) -> DispatchWorktreeRecord | None:
    """Return the lane-owned worktree for ``thread_id``, if registered."""
    with _connect() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT thread_id, worktree_path, branch_name, branch_point, "
            "last_dispatch_id FROM cursor_sdk_lane_worktrees WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def register_dispatch_worktree(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    thread_id: str | None = None,
) -> None:
    """Register a lane worktree; ``thread_id`` defaults to ``dispatch_id``."""
    register_lane_worktree(
        thread_id=thread_id or dispatch_id,
        worktree_path=worktree_path,
        branch_name=branch_name,
        branch_point=branch_point,
        last_dispatch_id=dispatch_id,
    )


def unregister_dispatch_worktree(*, dispatch_id: str) -> None:
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None or not record.thread_id:
        return
    unregister_lane_worktree(thread_id=record.thread_id)


def lookup_dispatch_worktree(*, dispatch_id: str) -> DispatchWorktreeRecord | None:
    """Resolve a worktree via last_dispatch_id, then thread_id == dispatch_id."""
    with _connect() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT thread_id, worktree_path, branch_name, branch_point, "
            "last_dispatch_id FROM cursor_sdk_lane_worktrees "
            "WHERE last_dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT thread_id, worktree_path, branch_name, branch_point, "
                "last_dispatch_id FROM cursor_sdk_lane_worktrees WHERE thread_id=?",
                (dispatch_id,),
            ).fetchone()
        if row is None:
            try:
                thread_row = conn.execute(
                    "SELECT thread_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                    (dispatch_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                thread_row = None
            if thread_row is not None and thread_row["thread_id"]:
                row = conn.execute(
                    "SELECT thread_id, worktree_path, branch_name, branch_point, "
                    "last_dispatch_id FROM cursor_sdk_lane_worktrees WHERE thread_id=?",
                    (thread_row["thread_id"],),
                ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def list_registered_worktrees_with_status() -> list[sqlite3.Row]:
    """Lane rows with live-writer status (NULL when the lane has no active dispatch)."""
    with _connect() as conn:
        ensure_worktree_schema(conn)
        return conn.execute(
            "SELECT w.thread_id, w.last_dispatch_id AS dispatch_id, "
            "w.worktree_path, w.branch_name, w.branch_point, "
            "w.salvage_refusal_count, w.quarantined_at, "
            "(SELECT d.status FROM cursor_sdk_dispatches d "
            " WHERE d.thread_id = w.thread_id AND COALESCE(d.read_only,0)=0 "
            " AND d.status IN ('admitted','running') LIMIT 1) AS status "
            "FROM cursor_sdk_lane_worktrees w"
        ).fetchall()


def _resolve_thread_id(*, thread_id: str | None, dispatch_id: str | None) -> str | None:
    if thread_id:
        return thread_id
    if not dispatch_id:
        return None
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is not None and record.thread_id:
        return record.thread_id
    return dispatch_id


def record_salvage_refusal(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
) -> int:
    """Increment consecutive salvage refusals; quarantine the row at 3. Return count."""
    key = _resolve_thread_id(thread_id=thread_id, dispatch_id=dispatch_id)
    if key is None:
        return 0
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees "
            "SET salvage_refusal_count = COALESCE(salvage_refusal_count, 0) + 1 "
            "WHERE thread_id=?",
            (key,),
        )
        row = conn.execute(
            "SELECT salvage_refusal_count FROM cursor_sdk_lane_worktrees "
            "WHERE thread_id=?",
            (key,),
        ).fetchone()
        count = int(row["salvage_refusal_count"]) if row is not None else 0
        if count >= _QUARANTINE_AFTER:
            conn.execute(
                "UPDATE cursor_sdk_lane_worktrees "
                "SET quarantined_at = COALESCE(quarantined_at, ?) "
                "WHERE thread_id=?",
                (_now(), key),
            )
        return count


def clear_salvage_refusal(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    """Reset refusal count and lift quarantine after a successful salvage."""
    key = _resolve_thread_id(thread_id=thread_id, dispatch_id=dispatch_id)
    if key is None:
        return
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees "
            "SET salvage_refusal_count = 0, quarantined_at = NULL "
            "WHERE thread_id=?",
            (key,),
        )


def worktree_is_quarantined(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
) -> bool:
    """True when the registry row is parked after consecutive salvage refusals."""
    key = _resolve_thread_id(thread_id=thread_id, dispatch_id=dispatch_id)
    if key is None:
        return False
    with _connect() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT quarantined_at FROM cursor_sdk_lane_worktrees WHERE thread_id=?",
            (key,),
        ).fetchone()
    return row is not None and bool(row["quarantined_at"])


def isolated_write_ceiling() -> int:
    """Regime-ON configured writer ceiling (``CURSOR_SDK_ISOLATED_WRITE_CEILING``)."""
    raw = os.environ.get("CURSOR_SDK_ISOLATED_WRITE_CEILING", "6")
    return max(1, int(raw))


def mintable_worktrees() -> int:
    """Remaining mint slots: ceiling minus live lanes with a worktree on disk."""
    ceiling = isolated_write_ceiling()
    try:
        rows = list_registered_worktrees_with_status()
    except sqlite3.OperationalError:
        return ceiling
    live_lanes: set[str] = set()
    for row in rows:
        status = row["status"]
        if status not in ("admitted", "running"):
            continue
        if Path(row["worktree_path"]).is_dir():
            live_lanes.add(str(row["thread_id"]))
    return max(0, ceiling - len(live_lanes))
