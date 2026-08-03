"""Dispatch workspace resolution for cursor-sdk (Lane-A vs isolated write trees.

S1a-1: Lane-A returns ``cfg.dispatch_workspace`` with zero runtime delta.
S1b: Lane-B mints or accepts a worktree under ``worktree_root``; lease key is
the resolved write-tree path.
S2: ``default_write_path_is_lane_a`` follows the durable regime switch (LB-1 off).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_sdk_lane_regime import lane_b_regime_active
from services.git_integration_worker.cursor_sdk_worktree import (
    is_managed_worktree,
    workspace_from_promoted_lease,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

Lane = Literal["A", "B"]


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
    lane_b_wire = (
        dispatch.lane == "B"
        or dispatch.worktree_isolated
        or dispatch.worktree_path
    )
    if lane_b_wire:
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
    """True while admit defaults to shared-master lease keys (regime OFF ⇒ Lane-A)."""
    return not lane_b_regime_active()


def write_lease_slots(
    lane: Lane | None = None,
    *,
    gate_limit: int | None = None,
) -> int:
    """Concurrently grantable write leases for the selected or default admit lane."""
    effective: Lane
    if lane is not None:
        effective = lane
    else:
        effective = "A" if default_write_path_is_lane_a() else "B"
    if effective == "A":
        return 1
    if gate_limit is not None:
        return max(1, gate_limit)
    std = max(1, int(os.environ.get("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")))
    op = max(1, int(os.environ.get("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")))
    return std + op


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
