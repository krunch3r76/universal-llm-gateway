"""SQLite registry and mint mutex for lane-owned Lane-B worktrees.

Registry rows are keyed by ``(source_repo, thread_id)`` so discharge and unpin
resolve removal authority to exactly one repo. Rows append to
``cursor_sdk_lane_worktree_journal`` on register/unregister/quarantine; if the
journal is unavailable, recovery falls back to git ground truth
(``git -C <repo> worktree list``) plus ``sdk.lane_b.discharged`` archive tags.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
from services.git_integration_worker.cursor_sdk_worktree_reconcile import (
    list_git_worktrees,
)

_MINT_MUTEX_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_mint_mutex (
    mutex_key     TEXT PRIMARY KEY,
    holder_id     TEXT NOT NULL,
    acquired_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_worktrees (
    source_repo           TEXT NOT NULL,
    thread_id             TEXT NOT NULL,
    worktree_path         TEXT NOT NULL,
    branch_name           TEXT NOT NULL,
    branch_point          TEXT NOT NULL,
    minted_at             TEXT NOT NULL,
    last_dispatch_id      TEXT,
    salvage_refusal_count INTEGER NOT NULL DEFAULT 0,
    quarantined_at        TEXT,
    PRIMARY KEY (source_repo, thread_id)
);
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_worktree_pins (
    source_repo     TEXT NOT NULL,
    thread_id       TEXT NOT NULL,
    dispatch_id     TEXT NOT NULL,
    worktree_path   TEXT NOT NULL,
    pinned_at       TEXT NOT NULL,
    released_at     TEXT,
    release_reason  TEXT,
    lock_reason     TEXT NOT NULL,
    PRIMARY KEY (source_repo, thread_id, dispatch_id)
);
CREATE INDEX IF NOT EXISTS idx_lane_worktree_pins_active
    ON cursor_sdk_lane_worktree_pins (source_repo, thread_id)
    WHERE released_at IS NULL;
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_worktree_journal (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    transition        TEXT NOT NULL,
    source_repo       TEXT NOT NULL,
    thread_id         TEXT NOT NULL,
    worktree_path     TEXT NOT NULL,
    branch_name       TEXT NOT NULL,
    last_dispatch_id  TEXT,
    trigger           TEXT,
    transitioned_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_worktrees_quarantine (
    source_repo           TEXT,
    thread_id             TEXT NOT NULL,
    worktree_path         TEXT NOT NULL,
    branch_name           TEXT NOT NULL,
    branch_point          TEXT NOT NULL,
    minted_at             TEXT NOT NULL,
    last_dispatch_id      TEXT,
    salvage_refusal_count INTEGER NOT NULL DEFAULT 0,
    quarantined_at        TEXT NOT NULL,
    quarantine_reason     TEXT NOT NULL
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
_GIT_TIMEOUT_S = 60.0
_SCHEMA_MIGRATED = False


@dataclass(frozen=True, slots=True)
class DispatchWorktreeRecord:
    """Registered Lane-B worktree metadata from the lane registry."""

    worktree_path: Path
    branch_name: str
    branch_point: str
    thread_id: str = ""
    last_dispatch_id: str | None = None
    source_repo: str = ""


@dataclass(frozen=True, slots=True)
class PinRow:
    """Active or historical pin row for a lane worktree."""

    source_repo: str
    thread_id: str
    dispatch_id: str
    worktree_path: str
    pinned_at: str
    released_at: str | None
    release_reason: str | None
    lock_reason: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _repo_slug(source_repo: Path) -> str:
    return source_repo.resolve().name


def repo_worktree_subroot(worktree_root: Path, source_repo: Path) -> Path:
    """Per-repo lane directory under the shared worktree root."""
    return worktree_root.resolve() / _repo_slug(source_repo)


def _candidate_repos(conn: sqlite3.Connection) -> list[Path]:
    repos: set[Path] = set()
    try:
        for row in conn.execute(
            "SELECT DISTINCT source_repo FROM cursor_sdk_dispatches "
            "WHERE source_repo IS NOT NULL AND source_repo != ''"
        ):
            if row[0]:
                repos.add(Path(str(row[0])).resolve())
    except sqlite3.OperationalError:
        pass
    from services.git_integration_worker.config import load_config

    repos.add(load_config().source_repo.resolve())
    return sorted(repos, key=str)


def _resolve_source_repo_for_path(
    conn: sqlite3.Connection,
    worktree_path: Path,
    *,
    last_dispatch_id: str | None,
) -> str | None:
    if last_dispatch_id:
        try:
            row = conn.execute(
                "SELECT source_repo FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (last_dispatch_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is not None and row["source_repo"]:
            return str(row["source_repo"])
    target = worktree_path.resolve()
    for repo in _candidate_repos(conn):
        for wt in list_git_worktrees(source_repo=repo):
            if wt.path.resolve() == target:
                return str(repo.resolve())
    return None


def _append_journal(
    conn: sqlite3.Connection,
    *,
    transition: str,
    source_repo: str,
    thread_id: str,
    worktree_path: str,
    branch_name: str,
    last_dispatch_id: str | None,
    trigger: str | None,
) -> None:
    conn.execute(
        "INSERT INTO cursor_sdk_lane_worktree_journal "
        "(transition, source_repo, thread_id, worktree_path, branch_name, "
        "last_dispatch_id, trigger, transitioned_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            transition,
            source_repo,
            thread_id,
            worktree_path,
            branch_name,
            last_dispatch_id,
            trigger,
            _now(),
        ),
    )


def _emit_registry_transition(
    *,
    transition: str,
    source_repo: str,
    thread_id: str,
    worktree_path: str,
    branch_name: str,
    last_dispatch_id: str | None,
    trigger: str | None,
) -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_registry_quarantined,
        emit_sdk_lane_b_registry_registered,
        emit_sdk_lane_b_registry_unregistered,
    )

    payload_kwargs = {
        "thread_id": thread_id,
        "source_repo": source_repo,
        "worktree_path": worktree_path,
        "branch_name": branch_name,
        "last_dispatch_id": last_dispatch_id,
        "trigger": trigger,
    }
    if transition == "registered":
        emit_sdk_lane_b_registry_registered(**payload_kwargs)
    elif transition == "unregistered":
        emit_sdk_lane_b_registry_unregistered(**payload_kwargs)
    elif transition == "quarantined":
        emit_sdk_lane_b_registry_quarantined(**payload_kwargs)


def _migrate_lane_worktrees_pk(conn: sqlite3.Connection) -> None:
    global _SCHEMA_MIGRATED
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(cursor_sdk_lane_worktrees)")
    }
    if not cols:
        return
    if "source_repo" in cols:
        _SCHEMA_MIGRATED = True
        return
    conn.execute("DROP TABLE IF EXISTS cursor_sdk_lane_worktrees_new")
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE cursor_sdk_lane_worktrees_new (
                source_repo           TEXT NOT NULL,
                thread_id             TEXT NOT NULL,
                worktree_path         TEXT NOT NULL,
                branch_name           TEXT NOT NULL,
                branch_point          TEXT NOT NULL,
                minted_at             TEXT NOT NULL,
                last_dispatch_id      TEXT,
                salvage_refusal_count INTEGER NOT NULL DEFAULT 0,
                quarantined_at        TEXT,
                PRIMARY KEY (source_repo, thread_id)
            )
            """
        )
        rows = conn.execute(
            "SELECT thread_id, worktree_path, branch_name, branch_point, minted_at, "
            "last_dispatch_id, salvage_refusal_count, quarantined_at "
            "FROM cursor_sdk_lane_worktrees"
        ).fetchall()
        for row in rows:
            resolved = _resolve_source_repo_for_path(
                conn,
                Path(row["worktree_path"]),
                last_dispatch_id=row["last_dispatch_id"],
            )
            if resolved is None:
                conn.execute(
                    "INSERT INTO cursor_sdk_lane_worktrees_quarantine "
                    "(source_repo, thread_id, worktree_path, branch_name, branch_point, "
                    "minted_at, last_dispatch_id, salvage_refusal_count, quarantined_at, "
                    "quarantine_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        None,
                        row["thread_id"],
                        row["worktree_path"],
                        row["branch_name"],
                        row["branch_point"],
                        row["minted_at"],
                        row["last_dispatch_id"],
                        row["salvage_refusal_count"],
                        row["quarantined_at"] or _now(),
                        "unresolved_source_repo",
                    ),
                )
                _emit_registry_transition(
                    transition="quarantined",
                    source_repo="",
                    thread_id=str(row["thread_id"]),
                    worktree_path=str(row["worktree_path"]),
                    branch_name=str(row["branch_name"]),
                    last_dispatch_id=row["last_dispatch_id"],
                    trigger="migration",
                )
                continue
            conn.execute(
                "INSERT INTO cursor_sdk_lane_worktrees_new "
                "(source_repo, thread_id, worktree_path, branch_name, branch_point, "
                "minted_at, last_dispatch_id, salvage_refusal_count, quarantined_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    resolved,
                    row["thread_id"],
                    row["worktree_path"],
                    row["branch_name"],
                    row["branch_point"],
                    row["minted_at"],
                    row["last_dispatch_id"],
                    row["salvage_refusal_count"],
                    row["quarantined_at"],
                ),
            )
        conn.execute("DROP TABLE cursor_sdk_lane_worktrees")
        conn.execute(
            "ALTER TABLE cursor_sdk_lane_worktrees_new "
            "RENAME TO cursor_sdk_lane_worktrees"
        )
        conn.execute("COMMIT")
        _SCHEMA_MIGRATED = True
    except Exception:
        conn.rollback()
        raise


def ensure_worktree_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MINT_MUTEX_DDL)
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(cursor_sdk_lane_worktrees)")
    }
    if cols and "source_repo" not in cols:
        _migrate_lane_worktrees_pk(conn)
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


def _row_to_pin(row: sqlite3.Row) -> PinRow:
    return PinRow(
        source_repo=str(row["source_repo"]),
        thread_id=str(row["thread_id"]),
        dispatch_id=str(row["dispatch_id"]),
        worktree_path=str(row["worktree_path"]),
        pinned_at=str(row["pinned_at"]),
        released_at=row["released_at"],
        release_reason=row["release_reason"],
        lock_reason=str(row["lock_reason"]),
    )


def pin_lane_worktree(
    conn: sqlite3.Connection,
    *,
    source_repo: Path,
    thread_id: str,
    dispatch_id: str,
    worktree_path: Path,
    lock_reason: str,
) -> PinRow:
    """Supersede any active pin for the lane pair and insert a new pin row."""
    repo_str = str(source_repo.resolve())
    wt_str = str(worktree_path.resolve())
    now = _now()
    conn.execute(
        "UPDATE cursor_sdk_lane_worktree_pins "
        "SET released_at=?, release_reason=? "
        "WHERE source_repo=? AND thread_id=? AND released_at IS NULL",
        (now, f"superseded_by:{dispatch_id}", repo_str, thread_id),
    )
    conn.execute(
        "INSERT INTO cursor_sdk_lane_worktree_pins "
        "(source_repo, thread_id, dispatch_id, worktree_path, pinned_at, "
        "released_at, release_reason, lock_reason) "
        "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)",
        (repo_str, thread_id, dispatch_id, wt_str, now, lock_reason),
    )
    _append_journal(
        conn,
        transition="pinned",
        source_repo=repo_str,
        thread_id=thread_id,
        worktree_path=wt_str,
        branch_name="",
        last_dispatch_id=dispatch_id,
        trigger="pin",
    )
    row = conn.execute(
        "SELECT source_repo, thread_id, dispatch_id, worktree_path, pinned_at, "
        "released_at, release_reason, lock_reason "
        "FROM cursor_sdk_lane_worktree_pins "
        "WHERE source_repo=? AND thread_id=? AND dispatch_id=?",
        (repo_str, thread_id, dispatch_id),
    ).fetchone()
    assert row is not None
    return _row_to_pin(row)


def release_pin(
    *,
    source_repo: Path,
    thread_id: str,
    dispatch_id: str,
    release_reason: str,
) -> None:
    """Mark the active pin released for ``(source_repo, thread_id, dispatch_id)``."""
    repo_str = str(source_repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktree_pins "
            "SET released_at=?, release_reason=? "
            "WHERE source_repo=? AND thread_id=? AND dispatch_id=? "
            "AND released_at IS NULL",
            (_now(), release_reason, repo_str, thread_id, dispatch_id),
        )


def active_pin(
    *,
    source_repo: Path,
    thread_id: str,
) -> PinRow | None:
    """Return the active pin for a lane pair, if any."""
    repo_str = str(source_repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT source_repo, thread_id, dispatch_id, worktree_path, pinned_at, "
            "released_at, release_reason, lock_reason "
            "FROM cursor_sdk_lane_worktree_pins "
            "WHERE source_repo=? AND thread_id=? AND released_at IS NULL "
            "ORDER BY pinned_at DESC LIMIT 1",
            (repo_str, thread_id),
        ).fetchone()
    if row is None:
        return None
    return _row_to_pin(row)


def list_active_pins(*, source_repo: Path | None = None) -> list[PinRow]:
    """List active pins, optionally scoped to one repo."""
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        if source_repo is not None:
            rows = conn.execute(
                "SELECT source_repo, thread_id, dispatch_id, worktree_path, pinned_at, "
                "released_at, release_reason, lock_reason "
                "FROM cursor_sdk_lane_worktree_pins "
                "WHERE source_repo=? AND released_at IS NULL",
                (str(source_repo.resolve()),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source_repo, thread_id, dispatch_id, worktree_path, pinned_at, "
                "released_at, release_reason, lock_reason "
                "FROM cursor_sdk_lane_worktree_pins WHERE released_at IS NULL"
            ).fetchall()
    return [_row_to_pin(row) for row in rows]


def master_mint_mutex_key(source_repo: Path) -> str:
    """Master-keyed mutex identity for serialized ``git worktree add``."""
    return str(source_repo.resolve())


def _try_acquire_mint_mutex(*, mutex_key: str, holder_id: str) -> bool:
    with ledger_connection() as conn:
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
    with ledger_connection() as conn:
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
        source_repo=str(row["source_repo"] or ""),
    )


def register_lane_worktree(
    *,
    source_repo: Path,
    thread_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    last_dispatch_id: str | None = None,
    trigger: str | None = "register",
) -> None:
    """Insert or replace the lane-owned worktree row."""
    from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
        clear_disposition,
    )

    repo_str = str(source_repo.resolve())
    clear_disposition(branch_name=branch_name)
    wt_str = str(worktree_path.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_lane_worktrees "
            "(source_repo, thread_id, worktree_path, branch_name, branch_point, "
            "minted_at, last_dispatch_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                repo_str,
                thread_id,
                wt_str,
                branch_name,
                branch_point,
                _now(),
                last_dispatch_id,
            ),
        )
        _append_journal(
            conn,
            transition="registered",
            source_repo=repo_str,
            thread_id=thread_id,
            worktree_path=wt_str,
            branch_name=branch_name,
            last_dispatch_id=last_dispatch_id,
            trigger=trigger,
        )
    _emit_registry_transition(
        transition="registered",
        source_repo=repo_str,
        thread_id=thread_id,
        worktree_path=wt_str,
        branch_name=branch_name,
        last_dispatch_id=last_dispatch_id,
        trigger=trigger,
    )


def touch_lane_worktree_dispatch(
    *,
    source_repo: Path,
    thread_id: str,
    dispatch_id: str,
) -> None:
    """Record the latest dispatch occupying an existing lane worktree."""
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees SET last_dispatch_id=? "
            "WHERE source_repo=? AND thread_id=?",
            (dispatch_id, str(source_repo.resolve()), thread_id),
        )


def unregister_lane_worktree(
    *,
    source_repo: Path,
    thread_id: str,
    trigger: str | None = "unregister",
) -> None:
    repo_str = str(source_repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT worktree_path, branch_name, last_dispatch_id "
            "FROM cursor_sdk_lane_worktrees WHERE source_repo=? AND thread_id=?",
            (repo_str, thread_id),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "DELETE FROM cursor_sdk_lane_worktrees WHERE source_repo=? AND thread_id=?",
            (repo_str, thread_id),
        )
        _append_journal(
            conn,
            transition="unregistered",
            source_repo=repo_str,
            thread_id=thread_id,
            worktree_path=str(row["worktree_path"]),
            branch_name=str(row["branch_name"]),
            last_dispatch_id=row["last_dispatch_id"],
            trigger=trigger,
        )
        _emit_registry_transition(
            transition="unregistered",
            source_repo=repo_str,
            thread_id=thread_id,
            worktree_path=str(row["worktree_path"]),
            branch_name=str(row["branch_name"]),
            last_dispatch_id=row["last_dispatch_id"],
            trigger=trigger,
        )


def lookup_lane_worktree(
    *,
    thread_id: str,
    source_repo: Path,
) -> DispatchWorktreeRecord | None:
    """Return the lane-owned worktree for ``(source_repo, thread_id)``."""
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT source_repo, thread_id, worktree_path, branch_name, branch_point, "
            "last_dispatch_id FROM cursor_sdk_lane_worktrees "
            "WHERE source_repo=? AND thread_id=?",
            (str(source_repo.resolve()), thread_id),
        ).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def register_dispatch_worktree(
    *,
    source_repo: Path,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    thread_id: str | None = None,
) -> None:
    """Register a lane worktree; ``thread_id`` defaults to ``dispatch_id``."""
    register_lane_worktree(
        source_repo=source_repo,
        thread_id=thread_id or dispatch_id,
        worktree_path=worktree_path,
        branch_name=branch_name,
        branch_point=branch_point,
        last_dispatch_id=dispatch_id,
    )


def unregister_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path | None = None,
) -> None:
    record = lookup_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if record is None or not record.thread_id:
        return
    repo = source_repo or Path(record.source_repo)
    unregister_lane_worktree(thread_id=record.thread_id, source_repo=repo)


def lookup_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path | None = None,
) -> DispatchWorktreeRecord | None:
    """Resolve a worktree via last_dispatch_id, then thread-scoped fallback."""
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        if source_repo is not None:
            repo_str = str(source_repo.resolve())
            row = conn.execute(
                "SELECT source_repo, thread_id, worktree_path, branch_name, "
                "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                "WHERE source_repo=? AND last_dispatch_id=?",
                (repo_str, dispatch_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT source_repo, thread_id, worktree_path, branch_name, "
                    "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                    "WHERE source_repo=? AND thread_id=?",
                    (repo_str, dispatch_id),
                ).fetchone()
        else:
            row = conn.execute(
                "SELECT source_repo, thread_id, worktree_path, branch_name, "
                "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                "WHERE last_dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT source_repo, thread_id, worktree_path, branch_name, "
                    "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                    "WHERE thread_id=?",
                    (dispatch_id,),
                ).fetchone()
        if row is None:
            try:
                thread_row = conn.execute(
                    "SELECT thread_id, source_repo FROM cursor_sdk_dispatches "
                    "WHERE dispatch_id=?",
                    (dispatch_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                thread_row = None
            if thread_row is not None and thread_row["thread_id"]:
                params: list[str] = [thread_row["thread_id"]]
                query = (
                    "SELECT source_repo, thread_id, worktree_path, branch_name, "
                    "branch_point, last_dispatch_id FROM cursor_sdk_lane_worktrees "
                    "WHERE thread_id=?"
                )
                dispatch_repo = thread_row["source_repo"]
                if source_repo is not None:
                    query += " AND source_repo=?"
                    params.append(str(source_repo.resolve()))
                elif dispatch_repo:
                    query += " AND source_repo=?"
                    params.append(str(dispatch_repo))
                row = conn.execute(query, params).fetchone()
    if row is None:
        return None
    return _row_to_record(row)


def list_registered_worktrees_with_status() -> list[sqlite3.Row]:
    """Lane rows with live-writer status (NULL when the lane has no active dispatch)."""
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        return conn.execute(
            "SELECT w.source_repo, w.thread_id, w.last_dispatch_id AS dispatch_id, "
            "w.worktree_path, w.branch_name, w.branch_point, "
            "w.salvage_refusal_count, w.quarantined_at, "
            "(SELECT d.status FROM cursor_sdk_dispatches d "
            " WHERE d.thread_id = w.thread_id AND d.status IN ('admitted','running') "
            " LIMIT 1) AS status "
            "FROM cursor_sdk_lane_worktrees w"
        ).fetchall()


def _resolve_thread_id(
    *,
    thread_id: str | None,
    dispatch_id: str | None,
    source_repo: Path | None = None,
) -> tuple[str | None, Path | None]:
    if thread_id and source_repo is not None:
        return thread_id, source_repo
    if thread_id:
        return thread_id, source_repo
    if not dispatch_id:
        return None, source_repo
    record = lookup_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if record is not None and record.thread_id:
        repo = source_repo or (
            Path(record.source_repo) if record.source_repo else None
        )
        return record.thread_id, repo
    return dispatch_id, source_repo


def record_salvage_refusal(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    source_repo: Path | None = None,
) -> int:
    """Increment consecutive salvage refusals; quarantine the row at 3. Return count."""
    key, repo = _resolve_thread_id(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if key is None or repo is None:
        return 0
    repo_str = str(repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees "
            "SET salvage_refusal_count = COALESCE(salvage_refusal_count, 0) + 1 "
            "WHERE source_repo=? AND thread_id=?",
            (repo_str, key),
        )
        row = conn.execute(
            "SELECT salvage_refusal_count FROM cursor_sdk_lane_worktrees "
            "WHERE source_repo=? AND thread_id=?",
            (repo_str, key),
        ).fetchone()
        count = int(row["salvage_refusal_count"]) if row is not None else 0
        if count >= _QUARANTINE_AFTER:
            conn.execute(
                "UPDATE cursor_sdk_lane_worktrees "
                "SET quarantined_at = COALESCE(quarantined_at, ?) "
                "WHERE source_repo=? AND thread_id=?",
                (_now(), repo_str, key),
            )
        return count


def clear_salvage_refusal(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    source_repo: Path | None = None,
) -> None:
    """Reset refusal count and lift quarantine after a successful salvage."""
    key, repo = _resolve_thread_id(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if key is None or repo is None:
        return
    repo_str = str(repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        conn.execute(
            "UPDATE cursor_sdk_lane_worktrees "
            "SET salvage_refusal_count = 0, quarantined_at = NULL "
            "WHERE source_repo=? AND thread_id=?",
            (repo_str, key),
        )


def worktree_is_quarantined(
    *,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    source_repo: Path | None = None,
) -> bool:
    """True when the registry row is parked after consecutive salvage refusals."""
    key, repo = _resolve_thread_id(
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    if key is None or repo is None:
        return False
    repo_str = str(repo.resolve())
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT quarantined_at FROM cursor_sdk_lane_worktrees "
            "WHERE source_repo=? AND thread_id=?",
            (repo_str, key),
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
            live_lanes.add(f"{row['source_repo']}:{row['thread_id']}")
    return max(0, ceiling - len(live_lanes))
