"""Per-dispatch worktree mint, prune, and orphan recovery (S1b A4/A5).

Lane-B dispatches mint an isolated tree under ``worktree_root`` with
master-keyed mint serialization and an explicitly resolved branch point.
Terminal dispatches prune their tree; boot/periodic reaper clears orphans
using the same sweeper shape as ``stale_lease_sweeper``.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import _connect
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

logger = get_logger(__name__)

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
_GIT_TIMEOUT_S = 60.0
_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._/-]+")


class WorktreeMintError(RuntimeError):
    """Raised when ``git worktree add`` fails after mutex acquisition."""


def master_mint_mutex_key(source_repo: Path) -> str:
    """Master-keyed mutex identity for serialized ``git worktree add``."""
    return str(source_repo.resolve())


def is_managed_worktree(path: Path, worktree_root: Path) -> bool:
    """True when ``path`` resolves under ``worktree_root``."""
    try:
        path.resolve().relative_to(worktree_root.resolve())
        return True
    except ValueError:
        return False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_worktree_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_MINT_MUTEX_DDL)


def _branch_name(dispatch_id: str) -> str:
    safe = _BRANCH_SAFE.sub("-", dispatch_id).strip("-") or "dispatch"
    return f"cursor-sdk/{safe}"


def _worktree_dir(worktree_root: Path, dispatch_id: str) -> Path:
    safe = _BRANCH_SAFE.sub("-", dispatch_id).strip("-") or "dispatch"
    return worktree_root / f"cursor-sdk-{safe}"


def resolve_master_branch_point(source_repo: Path, *, ref: str = "refs/heads/master") -> str:
    """Resolve an explicit commit for the worktree branch point (not tip sampling)."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "rev-parse", ref],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise WorktreeMintError(
            f"rev-parse {ref!r} failed for {source_repo}: {proc.stderr.strip()}"
        )
    sha = proc.stdout.strip()
    if not sha:
        raise WorktreeMintError(f"empty rev-parse for {ref!r} on {source_repo}")
    return sha


def _try_acquire_mint_mutex(*, mutex_key: str, holder_id: str) -> bool:
    with _connect() as conn:
        _ensure_worktree_schema(conn)
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


def _release_mint_mutex(*, mutex_key: str, holder_id: str) -> None:
    with _connect() as conn:
        _ensure_worktree_schema(conn)
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
            # Transient SQLite busy during concurrent mint connect/BEGIN — poll again.
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"mint mutex unavailable for {mutex_key!r} after {timeout_s:.0f}s"
            )
        time.sleep(_MINT_LOCK_POLL_S)


def _register_worktree(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
) -> None:
    with _connect() as conn:
        _ensure_worktree_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_dispatch_worktrees "
            "(dispatch_id, worktree_path, branch_name, branch_point, minted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (dispatch_id, str(worktree_path.resolve()), branch_name, branch_point, _now()),
        )


def _unregister_worktree(*, dispatch_id: str) -> None:
    with _connect() as conn:
        _ensure_worktree_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_dispatch_worktrees WHERE dispatch_id=?",
            (dispatch_id,),
        )


def _git_worktree_add_with_retry(
    *,
    source_repo: Path,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    attempts: int = 5,
) -> None:
    """Run ``git worktree add`` with short backoff on transient lock errors."""
    last_err = ""
    for attempt in range(attempts):
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo.resolve()),
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path),
                branch_point,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0:
            return
        last_err = proc.stderr.strip() or proc.stdout.strip()
        if "lock" not in last_err.lower() and attempt == attempts - 1:
            break
        time.sleep(_MINT_LOCK_POLL_S * (attempt + 1))
    raise WorktreeMintError(
        f"git worktree add failed for {worktree_path}: {last_err}"
    )


def mint_dispatch_worktree(
    *,
    source_repo: Path,
    worktree_root: Path,
    dispatch_id: str,
    branch_point: str | None = None,
) -> Path:
    """Mint an isolated dispatch worktree under master-keyed serialization."""
    worktree_root.mkdir(parents=True, exist_ok=True)
    wt_path = _worktree_dir(worktree_root, dispatch_id)
    if wt_path.exists():
        raise WorktreeMintError(f"worktree path already exists: {wt_path}")
    branch = _branch_name(dispatch_id)
    commit = branch_point or resolve_master_branch_point(source_repo)
    mutex_key = acquire_mint_mutex_blocking(source_repo=source_repo, holder_id=dispatch_id)
    try:
        _git_worktree_add_with_retry(
            source_repo=source_repo,
            worktree_path=wt_path,
            branch_name=branch,
            branch_point=commit,
        )
        _register_worktree(
            dispatch_id=dispatch_id,
            worktree_path=wt_path,
            branch_name=branch,
            branch_point=commit,
        )
        return wt_path.resolve()
    finally:
        _release_mint_mutex(mutex_key=mutex_key, holder_id=dispatch_id)


def accept_dispatch_worktree(
    *,
    worktree_path: Path,
    worktree_root: Path,
    dispatch_id: str,
    source_repo: Path,
) -> Path:
    """Validate and register a caller-supplied Lane-B worktree path."""
    resolved = worktree_path.resolve()
    if not is_managed_worktree(resolved, worktree_root):
        raise WorktreeMintError(
            f"worktree_path {resolved!r} is not under worktree_root {worktree_root!r}"
        )
    git_dir = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if git_dir.returncode != 0:
        raise WorktreeMintError(f"worktree_path is not a git worktree: {resolved!r}")
    branch_proc = subprocess.run(
        ["git", "-C", str(resolved), "branch", "--show-current"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    branch = branch_proc.stdout.strip() or _branch_name(dispatch_id)
    commit = resolve_master_branch_point(source_repo)
    _register_worktree(
        dispatch_id=dispatch_id,
        worktree_path=resolved,
        branch_name=branch,
        branch_point=commit,
    )
    return resolved


def prune_dispatch_worktree(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> bool:
    """Remove a registered dispatch worktree and drop its branch."""
    with _connect() as conn:
        _ensure_worktree_schema(conn)
        row = conn.execute(
            "SELECT worktree_path, branch_name FROM cursor_sdk_dispatch_worktrees "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return False
    wt_path = Path(row["worktree_path"])
    branch = row["branch_name"]
    if wt_path.is_dir():
        proc = subprocess.run(
            ["git", "-C", str(source_repo.resolve()), "worktree", "remove", "--force", str(wt_path)],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "worktree remove failed dispatch_id=%s path=%s err=%s",
                dispatch_id,
                wt_path,
                proc.stderr.strip(),
            )
    subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "branch", "-D", branch],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    _unregister_worktree(dispatch_id=dispatch_id)
    return True


def maybe_prune_worktree_on_terminal(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> bool:
    """Prune-on-terminal for minted Lane-B worktrees."""
    return prune_dispatch_worktree(dispatch_id=dispatch_id, source_repo=source_repo)


def active_managed_worktree_paths(*, worktree_root: Path) -> set[str]:
    """Resolved worktree paths for non-terminal dispatches under ``worktree_root``."""
    root = str(worktree_root.resolve())
    active: set[str] = set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT lease_key, source_repo, status FROM cursor_sdk_dispatches "
            "WHERE status IN ('admitted','running','queued','parked_waiting')"
        ).fetchall()
    for row in rows:
        key = row["lease_key"] or row["source_repo"]
        if not key:
            continue
        if key.startswith(root):
            active.add(str(Path(key).resolve()))
    return active


def reap_orphan_worktrees(
    *,
    source_repo: Path,
    worktree_root: Path,
) -> int:
    """Drop worktrees whose dispatch is terminal or missing (orphan recovery)."""
    reaped = 0
    active = active_managed_worktree_paths(worktree_root=worktree_root)
    with _connect() as conn:
        _ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT w.dispatch_id, w.worktree_path, d.status "
            "FROM cursor_sdk_dispatch_worktrees w "
            "LEFT JOIN cursor_sdk_dispatches d ON d.dispatch_id = w.dispatch_id"
        ).fetchall()
    terminal = {"completed", "failed", "cancelled"}
    for row in rows:
        wt_path = str(Path(row["worktree_path"]).resolve())
        status = row["status"]
        if wt_path in active:
            continue
        if status is None or status in terminal:
            if prune_dispatch_worktree(dispatch_id=row["dispatch_id"], source_repo=source_repo):
                reaped += 1
    return reaped


def _lookup_parent_lease_key(parent_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT lease_key, source_repo FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    if row is None:
        return None
    return row["lease_key"] or row["source_repo"]


def resolve_admit_binding(
    *,
    req: CursorDispatchRequest,
    source_repo: Path,
    worktree_root: Path,
    dispatch_workspace_default: Path,
) -> tuple[Path, str]:
    """Return ``(dispatch_workspace, lease_key)`` for ledger admit."""
    if req.nest_under:
        parent_key = _lookup_parent_lease_key(req.nest_under)
        if parent_key is None:
            raise WorktreeMintError(f"nest parent not found: {req.nest_under!r}")
        workspace = Path(parent_key).resolve()
        return workspace, str(workspace)

    if req.worktree_path:
        workspace = accept_dispatch_worktree(
            worktree_path=Path(req.worktree_path),
            worktree_root=worktree_root,
            dispatch_id=req.dispatch_id,
            source_repo=source_repo,
        )
        return workspace, str(workspace)

    if req.worktree_isolated:
        workspace = mint_dispatch_worktree(
            source_repo=source_repo,
            worktree_root=worktree_root,
            dispatch_id=req.dispatch_id,
        )
        return workspace, str(workspace)

    from services.git_integration_worker.cursor_sdk_workspace import lane_a_lease_key

    return dispatch_workspace_default, lane_a_lease_key(source_repo)


def workspace_from_promoted_lease(
    *,
    lease_key: str | None,
    source_repo: Path,
    worktree_root: Path,
    dispatch_workspace_default: Path,
) -> Path:
    """Resolve launch workspace for a promoted queued dispatch."""
    if lease_key and is_managed_worktree(Path(lease_key), worktree_root):
        return Path(lease_key).resolve()
    _ = source_repo
    return dispatch_workspace_default
