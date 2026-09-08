"""Lane-owned worktree mint and orphan recovery (S1b A4/A5).

Lane-B dispatches bind a stable tree under ``worktree_root`` keyed by
``thread_id`` (dir ``lane-{thread}``, branch ``cursor-sdk/lane-{thread}``).
Mint is once per lane; later admits reuse the tree. If the branch still
exists after the directory is gone, remint attaches the existing branch
instead of ``worktree add -b``. The reaper removes a lane tree only when
it has no live writer and the branch has merged.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
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
    lookup_lane_worktree,
    master_mint_mutex_key,
    register_lane_worktree,
    release_mint_mutex,
    repo_worktree_subroot,
    touch_lane_worktree_dispatch,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_MINT_LOCK_POLL_S = 0.02
_GIT_TIMEOUT_S = 60.0
_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._/-]+")

AdmitBindingKind = Literal["minted", "adopted", "reused", "resumed", "nested", "lane_a"]


@dataclass(frozen=True, slots=True)
class AdmitBindingResult:
    """Workspace + lease key for ledger admit, with binding provenance."""

    workspace: Path
    lease_key: str
    binding_kind: AdmitBindingKind


class WorktreeMintError(RuntimeError):
    """Raised when ``git worktree add`` fails after mutex acquisition.

    ``retryable`` is True only for transient lock contention that exhausted
    in-process backoff. Branch-exists, path-exists, and attach failures are
    permanent — an identical retry will fail identically.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def is_managed_worktree(path: Path, worktree_root: Path) -> bool:
    """True when ``path`` resolves under ``worktree_root``."""
    try:
        path.resolve().relative_to(worktree_root.resolve())
        return True
    except ValueError:
        return False


def lane_branch_name(thread_id: str) -> str:
    """Stable ``cursor-sdk/lane-{thread_id}`` branch for a lane."""
    safe = _BRANCH_SAFE.sub("-", thread_id).strip("-") or "lane"
    return f"cursor-sdk/lane-{safe}"


def lane_worktree_dir(
    worktree_root: Path,
    thread_id: str,
    *,
    source_repo: Path,
) -> Path:
    """Worktree directory ``lane-{thread_id}`` under per-repo subroot."""
    safe = _BRANCH_SAFE.sub("-", thread_id).strip("-") or "lane"
    return repo_worktree_subroot(worktree_root, source_repo) / f"lane-{safe}"


def _worktree_dir(
    worktree_root: Path,
    thread_id: str,
    *,
    source_repo: Path,
) -> Path:
    return lane_worktree_dir(worktree_root, thread_id, source_repo=source_repo)


def _branch_name(thread_id: str) -> str:
    return lane_branch_name(thread_id)


def resolve_master_branch_point(
    source_repo: Path, *, ref: str = "refs/heads/master"
) -> str:
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


def _is_transient_lock_error(err: str) -> bool:
    return "lock" in err.lower()


def _is_branch_exists_error(err: str) -> bool:
    lower = err.lower()
    return "already exists" in lower or "a branch named" in lower


def _branch_exists(source_repo: Path, branch_name: str) -> bool:
    """True when ``refs/heads/{branch_name}`` exists on ``source_repo``."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(source_repo.resolve()),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch_name}",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    return proc.returncode == 0


def _rev_parse(source_repo: Path, ref: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "rev-parse", ref],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise WorktreeMintError(
            f"rev-parse {ref!r} failed for {source_repo}: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _git_worktree_add(
    *,
    source_repo: Path,
    args: list[str],
    worktree_path: Path,
    attempts: int = 5,
) -> None:
    """Run ``git worktree add`` with short backoff on transient lock errors."""
    last_err = ""
    for attempt in range(attempts):
        proc = subprocess.run(
            ["git", "-C", str(source_repo.resolve()), "worktree", "add", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0:
            return
        last_err = proc.stderr.strip() or proc.stdout.strip()
        if not _is_transient_lock_error(last_err):
            break
        time.sleep(_MINT_LOCK_POLL_S * (attempt + 1))
    raise WorktreeMintError(
        f"git worktree add failed for {worktree_path}: {last_err}",
        retryable=_is_transient_lock_error(last_err),
    )


def _git_worktree_add_with_retry(
    *,
    source_repo: Path,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    attempts: int = 5,
) -> None:
    """Create ``branch_name`` at ``branch_point``, or attach it if it already exists."""
    try:
        _git_worktree_add(
            source_repo=source_repo,
            args=["-b", branch_name, str(worktree_path), branch_point],
            worktree_path=worktree_path,
            attempts=attempts,
        )
    except WorktreeMintError as exc:
        if not _is_branch_exists_error(str(exc)):
            raise
        _git_worktree_add(
            source_repo=source_repo,
            args=[str(worktree_path), branch_name],
            worktree_path=worktree_path,
            attempts=attempts,
        )


def mint_dispatch_worktree(
    *,
    source_repo: Path,
    worktree_root: Path,
    dispatch_id: str,
    thread_id: str | None = None,
    branch_point: str | None = None,
) -> Path:
    """Mint a lane-owned worktree once under master-keyed serialization."""
    lane_id = thread_id or dispatch_id
    subroot = repo_worktree_subroot(worktree_root, source_repo)
    subroot.mkdir(parents=True, exist_ok=True)
    wt_path = _worktree_dir(worktree_root, lane_id, source_repo=source_repo)
    if wt_path.exists():
        raise WorktreeMintError(f"worktree path already exists: {wt_path}")
    branch = _branch_name(lane_id)
    commit = branch_point or resolve_master_branch_point(source_repo)
    mutex_key = acquire_mint_mutex_blocking(
        source_repo=source_repo, holder_id=dispatch_id
    )
    try:
        if _branch_exists(source_repo, branch):
            _git_worktree_add(
                source_repo=source_repo,
                args=[str(wt_path), branch],
                worktree_path=wt_path,
            )
            commit = _rev_parse(source_repo, f"refs/heads/{branch}")
        else:
            _git_worktree_add_with_retry(
                source_repo=source_repo,
                worktree_path=wt_path,
                branch_name=branch,
                branch_point=commit,
            )
        register_lane_worktree(
            source_repo=source_repo,
            thread_id=lane_id,
            worktree_path=wt_path,
            branch_name=branch,
            branch_point=commit,
            last_dispatch_id=dispatch_id,
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
    thread_id: str | None = None,
) -> Path:
    """Validate and register a caller-supplied Lane-B worktree path."""
    lane_id = thread_id or dispatch_id
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
    branch = branch_proc.stdout.strip() or _branch_name(lane_id)
    commit = resolve_master_branch_point(source_repo)
    register_lane_worktree(
        source_repo=source_repo,
        thread_id=lane_id,
        worktree_path=resolved,
        branch_name=branch,
        branch_point=commit,
        last_dispatch_id=dispatch_id,
    )
    return resolved


def lookup_parent_lease_key(parent_id: str) -> str | None:
    with ledger_connection() as conn:
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
    hub: Path,
    worktree_root: Path,
    dispatch_workspace_default: Path,
    lane: Literal["A", "B"],
) -> AdmitBindingResult:
    """Return workspace, lease key, and binding kind for ledger admit."""
    if req.resume_of:
        parent_key = lookup_parent_lease_key(req.resume_of)
        if parent_key is None:
            raise WorktreeMintError(f"resume parent not found: {req.resume_of!r}")
        # The SDK agent store is HOME- and cwd-keyed (store-A): the child must
        # launch in the parent's cwd or ``resume_agent`` raises AgentNotFound.
        # A Lane-B lease key *is* the worktree the parent ran in; a Lane-A lease
        # key is the repo path while the parent ran in dispatch_workspace_default
        # (steer-restart live proof 743f9f4b8aff-r1, 2026-09-08).
        workspace = workspace_from_promoted_lease(
            lease_key=parent_key,
            source_repo=source_repo,
            worktree_root=worktree_root,
            dispatch_workspace_default=dispatch_workspace_default,
        )
        if req.thread_id:
            touch_lane_worktree_dispatch(
                source_repo=source_repo,
                thread_id=req.thread_id,
                dispatch_id=req.dispatch_id,
            )
        return AdmitBindingResult(
            workspace=workspace,
            lease_key=parent_key,
            binding_kind="resumed",
        )

    if req.nest_under:
        parent_key = lookup_parent_lease_key(req.nest_under)
        if parent_key is None:
            raise WorktreeMintError(f"nest parent not found: {req.nest_under!r}")
        workspace = Path(parent_key).resolve()
        return AdmitBindingResult(
            workspace=workspace,
            lease_key=str(workspace),
            binding_kind="nested",
        )

    if lane == "B":
        existing = lookup_lane_worktree(
            thread_id=req.thread_id,
            source_repo=source_repo,
        )
        if existing is not None and existing.worktree_path.is_dir():
            touch_lane_worktree_dispatch(
                source_repo=source_repo,
                thread_id=req.thread_id,
                dispatch_id=req.dispatch_id,
            )
            workspace = existing.worktree_path.resolve()
            return AdmitBindingResult(
                workspace=workspace,
                lease_key=str(workspace),
                binding_kind="reused",
            )

        expected = lane_worktree_dir(
            worktree_root,
            req.thread_id,
            source_repo=source_repo,
        )
        if expected.is_dir():
            workspace = accept_dispatch_worktree(
                worktree_path=expected,
                worktree_root=worktree_root,
                dispatch_id=req.dispatch_id,
                source_repo=source_repo,
                thread_id=req.thread_id,
            )
            return AdmitBindingResult(
                workspace=workspace,
                lease_key=str(workspace),
                binding_kind="adopted",
            )

        if req.worktree_path:
            workspace = accept_dispatch_worktree(
                worktree_path=Path(req.worktree_path),
                worktree_root=worktree_root,
                dispatch_id=req.dispatch_id,
                source_repo=source_repo,
                thread_id=req.thread_id,
            )
            return AdmitBindingResult(
                workspace=workspace,
                lease_key=str(workspace),
                binding_kind="adopted",
            )

        workspace = mint_dispatch_worktree(
            source_repo=source_repo,
            worktree_root=worktree_root,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
        )
        return AdmitBindingResult(
            workspace=workspace,
            lease_key=str(workspace),
            binding_kind="minted",
        )

    from services.git_integration_worker.cursor_sdk_workspace import lane_a_lease_key

    if source_repo.resolve() == hub.resolve():
        return AdmitBindingResult(
            workspace=dispatch_workspace_default,
            lease_key=lane_a_lease_key(source_repo),
            binding_kind="lane_a",
        )
    resolved = source_repo.resolve()
    return AdmitBindingResult(
        workspace=resolved,
        lease_key=lane_a_lease_key(source_repo),
        binding_kind="lane_a",
    )


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


def pin_lane_worktree_on_admit(
    *,
    source_repo: Path,
    thread_id: str,
    dispatch_id: str,
    worktree_path: Path,
) -> str:
    """Registry pin + git lock after admit binding (S1 Leg B)."""
    from services.git_integration_worker.cursor_sdk_events import emit_sdk_lane_b_pinned
    from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
        ledger_connection,
    )
    from services.git_integration_worker.cursor_sdk_worktree_lock import (
        lock_lane_worktree,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        ensure_worktree_schema,
        pin_lane_worktree,
    )

    lock_reason = lock_lane_worktree(
        source_repo,
        worktree_path,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        pin_lane_worktree(
            conn,
            source_repo=source_repo,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            worktree_path=worktree_path,
            lock_reason=lock_reason,
        )
    emit_sdk_lane_b_pinned(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        worktree_path=str(worktree_path.resolve()),
        lock_reason=lock_reason,
    )
    return lock_reason


__all__ = [
    "AdmitBindingKind",
    "AdmitBindingResult",
    "DispatchWorktreeRecord",
    "PruneResult",
    "ReapSweepResult",
    "WorktreeMintError",
    "accept_dispatch_worktree",
    "is_managed_worktree",
    "lane_branch_name",
    "lane_worktree_dir",
    "lookup_dispatch_worktree",
    "lookup_lane_worktree",
    "lookup_parent_lease_key",
    "master_mint_mutex_key",
    "maybe_prune_worktree_on_terminal",
    "mint_dispatch_worktree",
    "pin_lane_worktree_on_admit",
    "prune_dispatch_worktree",
    "reap_orphan_worktrees",
    "resolve_admit_binding",
    "resolve_master_branch_point",
    "workspace_from_promoted_lease",
]
