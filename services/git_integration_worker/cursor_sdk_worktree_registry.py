"""SQLite registry and mint mutex for Lane-B dispatch worktrees."""

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
CREATE TABLE IF NOT EXISTS cursor_sdk_dispatch_worktrees (
    dispatch_id   TEXT PRIMARY KEY,
    worktree_path TEXT NOT NULL,
    branch_name   TEXT NOT NULL,
    branch_point  TEXT NOT NULL,
    minted_at     TEXT NOT NULL
);
"""

_MINT_LOCK_POLL_S = 0.02
_MINT_LOCK_TIMEOUT_S = 120.0


@dataclass(frozen=True, slots=True)
class DispatchWorktreeRecord:
    """Registered Lane-B worktree metadata from the dispatch ledger."""

    worktree_path: Path
    branch_name: str
    branch_point: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_worktree_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MINT_MUTEX_DDL)


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


def register_dispatch_worktree(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
) -> None:
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
    )

    clear_disposition(branch_name=branch_name)
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatch_worktrees "
            "(dispatch_id, worktree_path, branch_name, branch_point, minted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (dispatch_id, str(worktree_path.resolve()), branch_name, branch_point, _now()),
        )


def unregister_dispatch_worktree(*, dispatch_id: str) -> None:
    with _connect() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_dispatch_worktrees WHERE dispatch_id=?",
            (dispatch_id,),
        )


def lookup_dispatch_worktree(*, dispatch_id: str) -> DispatchWorktreeRecord | None:
    """Return registered worktree metadata for a dispatch, if any."""
    with _connect() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT worktree_path, branch_name, branch_point "
            "FROM cursor_sdk_dispatch_worktrees WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return DispatchWorktreeRecord(
        worktree_path=Path(row["worktree_path"]),
        branch_name=row["branch_name"],
        branch_point=row["branch_point"],
    )


def list_registered_worktrees_with_status() -> list[sqlite3.Row]:
    with _connect() as conn:
        ensure_worktree_schema(conn)
        return conn.execute(
            "SELECT w.dispatch_id, w.worktree_path, d.status "
            "FROM cursor_sdk_dispatch_worktrees w "
            "LEFT JOIN cursor_sdk_dispatches d ON d.dispatch_id = w.dispatch_id"
        ).fetchall()


def isolated_write_ceiling() -> int:
    """Regime-ON configured writer ceiling (``CURSOR_SDK_ISOLATED_WRITE_CEILING``)."""
    raw = os.environ.get("CURSOR_SDK_ISOLATED_WRITE_CEILING", "4")
    return max(1, int(raw))


def mintable_worktrees() -> int:
    """Remaining mint slots: ceiling minus live admitted/running worktrees on disk."""
    ceiling = isolated_write_ceiling()
    try:
        rows = list_registered_worktrees_with_status()
    except sqlite3.OperationalError:
        return ceiling
    live = 0
    for row in rows:
        status = row["status"]
        if status not in ("admitted", "running"):
            continue
        if Path(row["worktree_path"]).is_dir():
            live += 1
    return max(0, ceiling - live)
