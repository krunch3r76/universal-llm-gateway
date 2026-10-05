"""Remint a vanished Lane-B worktree so spawn does not die as ENOENT.

Admit already refuses a missing isolation tree. Bridge launch is later,
after the capacity slot wait, and does not re-stat. Friction 37813: admit
returned admitted=true, then ``launch_bridge`` used a deleted
``lane-{thread}`` directory and the Node bridge died
``spawn_enoent_missing_cwd``. A later admit reminted and succeeded.

This module is the spawn (and vanished-pin) remint: prune stale git
worktree metadata, then ``mint_dispatch_worktree`` so an existing lane
branch is re-attached. ``reconcile`` already imports
``remint_lane_worktree``; the module was missing.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.config import load_config
from services.git_integration_worker.cursor_sdk_capture_binding import CaptureBinding
from services.git_integration_worker.cursor_sdk_dispatch_context import (
    SdkDispatchContext,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    mint_dispatch_worktree,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0


def _prune_stale_worktree_metadata(*, source_repo: Path) -> None:
    """Drop git worktree entries whose directories were deleted."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "worktree", "prune"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "git worktree prune failed before remint repo=%s err=%s",
            source_repo,
            proc.stderr.strip(),
        )


def remint_lane_worktree(
    *,
    source_repo: Path,
    dispatch_id: str,
    thread_id: str,
    worktree_root: Path | None = None,
) -> Path:
    """Recreate the lane worktree for ``thread_id`` (attach existing branch).

    ``worktree_root`` defaults to ``load_config().worktree_root`` so
    ``sweep_vanished_pinned_worktrees`` can call this with the reconcile
    signature (source_repo, dispatch_id, thread_id).
    """
    root = worktree_root if worktree_root is not None else load_config().worktree_root
    _prune_stale_worktree_metadata(source_repo=source_repo)
    try:
        minted = mint_dispatch_worktree(
            source_repo=source_repo,
            worktree_root=root,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
    except WorktreeMintError as exc:
        if "already exists" in str(exc).lower():
            from services.git_integration_worker.cursor_sdk_worktree import (
                lane_worktree_dir,
            )

            existing = lane_worktree_dir(
                root, thread_id, source_repo=source_repo
            ).resolve()
            if existing.is_dir():
                return existing
        raise
    return minted


def ensure_spawn_workspace(
    ctx: SdkDispatchContext,
    *,
    worktree_root: Path | None = None,
) -> SdkDispatchContext:
    """Return ctx whose Lane-B ``dispatch_workspace`` exists, reminting if needed.

    Lane A is unchanged. A remint that lands on a different resolved path
    replaces ``dispatch_workspace`` and the capture write tree so
    ``local.cwd`` and ``launch_bridge(workspace=)`` agree.
    """
    if ctx.lane != "B":
        return ctx
    if ctx.dispatch_workspace.is_dir():
        return ctx
    logger.warning(
        "lane worktree missing at spawn; reminting dispatch_id=%s thread_id=%s path=%s",
        ctx.dispatch_id,
        ctx.thread_id,
        ctx.dispatch_workspace,
    )
    minted = remint_lane_worktree(
        source_repo=ctx.hub,
        dispatch_id=ctx.dispatch_id,
        thread_id=ctx.thread_id,
        worktree_root=worktree_root,
    )
    if not minted.is_dir():
        raise WorktreeMintError(
            "lane worktree missing at bridge spawn and remint left no directory: "
            f"{minted}",
            retryable=True,
        )
    if minted.resolve() == ctx.dispatch_workspace.resolve():
        return ctx
    binding = CaptureBinding(
        lane="B",
        write_tree=minted.resolve(),
        receipt_tree=ctx.hub.resolve(),
        mount_root=minted.resolve(),
        repo_roots=(minted.resolve(),),
    )
    return replace(ctx, dispatch_workspace=minted, capture_binding=binding)
