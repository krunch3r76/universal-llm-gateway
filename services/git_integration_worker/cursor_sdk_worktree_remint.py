"""Spawn refuse and vanished-pin remint for missing Lane-B worktrees.

Admit already refuses a missing isolation tree. Bridge launch is later,
after the capacity slot wait, and does not re-stat. Friction 37813: admit
returned admitted=true, then ``launch_bridge`` used a deleted
``lane-{thread}`` directory and the Node bridge died
``spawn_enoent_missing_cwd``. A later admit reminted and succeeded.

Post-admit lanes are ``git worktree lock``ed, so spawn-time remint cannot
reattach (CDP review 15067#2). Spawn therefore refuses with a retryable
``WorktreeMintError``; remint stays on the next admit. ``remint_lane_worktree``
is for the vanished-pin sweep only.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.config import load_config
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
    signature (source_repo, dispatch_id, thread_id). Callers must catch
    ``WorktreeMintError``: locked vanished pins fail prune/add.
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


def ensure_spawn_workspace(ctx: SdkDispatchContext) -> SdkDispatchContext:
    """Refuse Lane-B spawn when ``dispatch_workspace`` is gone.

    Lane A is unchanged. A present Lane-B tree is unchanged. Missing Lane-B
    raises retryable ``WorktreeMintError`` so the closeout is
    ``CURSOR_WORKTREE_MINT_FAILED`` instead of ``CURSOR_SDK_BRIDGE_SPAWN_CWD``.
    Remint is admit's job (correct repo and inherited lane thread).
    """
    if ctx.lane != "B":
        return ctx
    if ctx.dispatch_workspace.is_dir():
        return ctx
    logger.warning(
        "lane worktree missing at spawn; refusing remint dispatch_id=%s "
        "thread_id=%s path=%s",
        ctx.dispatch_id,
        ctx.thread_id,
        ctx.dispatch_workspace,
    )
    raise WorktreeMintError(
        "lane worktree missing at bridge spawn; remint is admit-time "
        f"(path={ctx.dispatch_workspace})",
        retryable=True,
    )
