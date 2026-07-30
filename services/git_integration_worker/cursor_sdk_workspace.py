"""Dispatch workspace resolution for cursor-sdk (Lane-A vs isolated write trees.

S1a-1: Lane-A returns ``cfg.dispatch_workspace`` with zero runtime delta.
S1b: Lane-B mints or accepts a worktree under ``worktree_root``; lease key is
the resolved write-tree path.
"""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_worktree import (
    is_managed_worktree,
    workspace_from_promoted_lease,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def resolve_dispatch_workspace(
    dispatch: CursorDispatchRequest,
    cfg: WorkerConfig,
    *,
    dispatch_workspace: Path | None = None,
    lease_key: str | None = None,
) -> Path:
    """Return the filesystem workspace used for agent ``cwd`` and effect paths.

    Lane-A (default): ``cfg.dispatch_workspace``. Lane-B callers supply a minted
    or accepted write-tree path via ``dispatch_workspace`` or ``lease_key``.
    """
    if dispatch_workspace is not None:
        return dispatch_workspace
    if lease_key and is_managed_worktree(Path(lease_key), cfg.worktree_root):
        return Path(lease_key).resolve()
    if dispatch.worktree_isolated or dispatch.worktree_path:
        raise ValueError(
            "Lane-B dispatch requires minted dispatch_workspace before resolve"
        )
    return cfg.dispatch_workspace


def lane_a_lease_key(source_repo: Path) -> str:
    """Writer lease identity for Lane-A dispatches (resolved source repo path)."""
    return str(source_repo.resolve())


def lane_b_lease_key(dispatch_workspace: Path) -> str:
    """Writer lease identity for Lane-B dispatches (resolved worktree path)."""
    return str(dispatch_workspace.resolve())


def default_write_path_is_lane_a() -> bool:
    """True while admit defaults to shared-master lease keys (pre-S1b default)."""
    return True


def write_lease_slots() -> int:
    """Concurrently grantable write leases under current default admit binding."""
    if default_write_path_is_lane_a():
        return 1
    raise NotImplementedError("Lane-B default write headroom is an M1 concern")


def resolve_promoted_workspace(
    *,
    lease_key: str | None,
    source_repo: Path,
    cfg: WorkerConfig,
) -> Path:
    """Launch workspace for a promoted queued dispatch."""
    return workspace_from_promoted_lease(
        lease_key=lease_key,
        source_repo=source_repo,
        worktree_root=cfg.worktree_root,
        dispatch_workspace_default=cfg.dispatch_workspace,
    )
