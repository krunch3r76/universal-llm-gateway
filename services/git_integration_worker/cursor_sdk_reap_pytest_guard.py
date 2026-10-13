"""Refuse Lane-B reap against the live hub while pytest is active."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.config import (
    _DEFAULT_SOURCE_REPO,
    _DEFAULT_WORKTREE_ROOT,
)

logger = get_logger(__name__)

_REAL_HUB_REPO = Path(_DEFAULT_SOURCE_REPO)
# Repo-scoped lane trees: ``{worktree_root}/{repo_basename}/lane-{thread_id}``.
_REAL_HUB_WORKTREE_STORE = (
    Path(_DEFAULT_WORKTREE_ROOT) / _REAL_HUB_REPO.name
)


def refuse_real_hub_reap_under_pytest(
    *,
    source_repo: Path,
    worktree_root: Path | None = None,
) -> None:
    """Raise when pytest would mutate the production hub checkout or lane store."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_reap_refused_pytest,
    )

    try:
        repo = source_repo.resolve()
    except OSError:
        repo = source_repo
    hub = _REAL_HUB_REPO.resolve()
    if repo == hub:
        detail = f"refusing reap against the live hub repo under pytest: {repo}"
        emit_sdk_lane_b_reap_refused_pytest(
            source_repo=repo,
            worktree_root=worktree_root,
            detail=detail,
        )
        raise RuntimeError(detail)
    if worktree_root is None:
        return
    try:
        wt_root = worktree_root.resolve()
    except OSError:
        wt_root = worktree_root
    store = _REAL_HUB_WORKTREE_STORE.resolve()
    if wt_root == store or wt_root.is_relative_to(store) or store.is_relative_to(wt_root):
        detail = (
            "refusing reap against the live hub worktree store under pytest: "
            f"worktree_root={wt_root} store={store}"
        )
        emit_sdk_lane_b_reap_refused_pytest(
            source_repo=repo,
            worktree_root=wt_root,
            detail=detail,
        )
        raise RuntimeError(detail)


def log_reap_pass(
    *,
    pass_name: str,
    source_repo: Path,
    action_count: int,
) -> None:
    """One audit line per reap entrypoint: caller identity and intended action count."""
    argv = sys.argv
    argv_slice = argv[:3]
    while len(argv_slice) < 3:
        argv_slice.append("")
    logger.info(
        "lane_b reap pass pass=%s pid=%s argv0=%s argv1=%s argv2=%s repo=%s action_count=%s",
        pass_name,
        os.getpid(),
        argv_slice[0],
        argv_slice[1],
        argv_slice[2],
        source_repo.resolve(),
        action_count,
    )
