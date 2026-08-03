"""Per-dispatch worktree mint, prune, and orphan recovery (S1b A4/A5).

Lane-B dispatches mint an isolated tree under ``worktree_root`` with
master-keyed mint serialization and an explicitly resolved branch point.
Terminal dispatches prune their tree; boot/periodic reaper clears orphans
using the same sweeper shape as ``stale_lease_sweeper``.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Literal

from services.git_integration_worker.cursor_dispatch_ledger import _connect
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    PruneResult,
    ReapSweepResult,
    maybe_prune_worktree_on_terminal,
    prune_dispatch_worktree,
    reap_orphan_worktrees,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    DispatchWorktreeRecord,
    acquire_mint_mutex_blocking,
    lookup_dispatch_worktree,
    master_mint_mutex_key,
    register_dispatch_worktree,
    release_mint_mutex,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_MINT_LOCK_POLL_S = 0.02
_GIT_TIMEOUT_S = 60.0
_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._/-]+")


class WorktreeMintError(RuntimeError):
    """Raised when ``git worktree add`` fails after mutex acquisition."""


def is_managed_worktree(path: Path, worktree_root: Path) -> bool:
    """True when ``path`` resolves under ``worktree_root``."""
    try:
        path.resolve().relative_to(worktree_root.resolve())
        return True
    except ValueError:
        return False


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
        register_dispatch_worktree(
            dispatch_id=dispatch_id,
            worktree_path=wt_path,
            branch_name=branch,
            branch_point=commit,
        )
        return wt_path.resolve()
    finally:
        release_mint_mutex(mutex_key=mutex_key, holder_id=dispatch_id)


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
    register_dispatch_worktree(
        dispatch_id=dispatch_id,
        worktree_path=resolved,
        branch_name=branch,
        branch_point=commit,
    )
    return resolved


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
    lane: Literal["A", "B"],
) -> tuple[Path, str]:
    """Return ``(dispatch_workspace, lease_key)`` for ledger admit."""
    if req.nest_under:
        parent_key = _lookup_parent_lease_key(req.nest_under)
        if parent_key is None:
            raise WorktreeMintError(f"nest parent not found: {req.nest_under!r}")
        workspace = Path(parent_key).resolve()
        return workspace, str(workspace)

    if lane == "B":
        if req.worktree_path:
            workspace = accept_dispatch_worktree(
                worktree_path=Path(req.worktree_path),
                worktree_root=worktree_root,
                dispatch_id=req.dispatch_id,
                source_repo=source_repo,
            )
            return workspace, str(workspace)

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


__all__ = [
    "DispatchWorktreeRecord",
    "PruneResult",
    "ReapSweepResult",
    "WorktreeMintError",
    "accept_dispatch_worktree",
    "is_managed_worktree",
    "lookup_dispatch_worktree",
    "master_mint_mutex_key",
    "maybe_prune_worktree_on_terminal",
    "mint_dispatch_worktree",
    "prune_dispatch_worktree",
    "reap_orphan_worktrees",
    "resolve_admit_binding",
    "resolve_master_branch_point",
    "workspace_from_promoted_lease",
]
