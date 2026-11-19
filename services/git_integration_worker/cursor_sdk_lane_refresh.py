"""Refresh a reused Lane-B worktree base onto hub master when safe.

Invariants:
- No live writer: ``sibling_non_terminal_dispatch_on_thread`` (excluding admitting dispatch).
- Content landed: ``merge-base --is-ancestor`` → ``merge --ff-only``; else non-empty
  ``git cherry`` with every line ``-`` → ``archive_branch`` then ``reset --keep``.
- Archive before reset: ``archive_branch`` must succeed before ``reset --keep``.
- No hard reset: only ``reset --keep`` and ``merge --ff-only``.
- Fail-closed on git or state errors: emit ``refused`` and raise ``StaleLaneError``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_archive import archive_branch
from services.git_integration_worker.cursor_sdk_worktree import WorktreeMintError
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    sibling_non_terminal_dispatch_on_thread,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    acquire_mint_mutex_blocking,
    lookup_lane_worktree,
    release_mint_mutex,
    update_lane_branch_point,
    worktree_is_quarantined,
)

logger = get_logger(__name__)
_GIT_TIMEOUT_S = 60.0
_git_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None


class StaleLaneError(WorktreeMintError):
    """Lane base cannot be advanced safely; omit ``reuse_thread`` on retry."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    global _git_runner
    if _git_runner is None:
        def _run(cwd: Path, *a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(cwd.resolve()), *a],
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_S,
                check=False,
            )

        _git_runner = _run
    return _git_runner(cwd, *args)


def _emit(
    outcome: str,
    *,
    ctx: dict[str, object] | None = None,
    dispatch_id: str,
    thread_id: str,
    old_tip: str | None,
    behind: int,
    new_tip: str | None = None,
) -> None:
    if ctx is not None and ctx.get("event_emitted"):
        return
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_base_refreshed,
    )

    try:
        emit_sdk_lane_b_base_refreshed(
            outcome=outcome,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            old_tip=old_tip,
            new_tip=new_tip,
            behind_by=behind,
        )
        if ctx is not None:
            ctx["event_emitted"] = True
    except Exception:
        logger.exception("base_refreshed emit failed outcome=%s", outcome)


def _emit_ctx(
    ctx: dict[str, object],
    outcome: str,
    *,
    new_tip: str | None = None,
) -> None:
    old = ctx["old_tip"]
    _emit(
        outcome,
        ctx=ctx,
        dispatch_id=str(ctx["dispatch_id"]),
        thread_id=str(ctx["thread_id"]),
        old_tip=old,  # type: ignore[arg-type]
        new_tip=new_tip if new_tip is not None else old,  # type: ignore[arg-type]
        behind=int(ctx["behind"]),  # type: ignore[arg-type]
    )


def _refuse(msg: str, *, ctx: dict[str, object]) -> None:
    _emit_ctx(ctx, "refused")
    raise StaleLaneError(msg)


def _emit_refused_from_ctx(ctx: dict[str, object]) -> None:
    _emit_ctx(ctx, "refused")


def refresh_landed_lane_base(
    *,
    source_repo: Path,
    worktree_path: Path,
    branch_name: str,
    branch_point: str,
    thread_id: str,
    dispatch_id: str,
) -> None:
    repo, wt = source_repo.resolve(), worktree_path.resolve()
    ctx: dict[str, object] = {
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
        "old_tip": None,
        "behind": 0,
    }
    mutex_key = None
    try:
        try:
            mutex_key = acquire_mint_mutex_blocking(source_repo=repo, holder_id=dispatch_id)
        except TimeoutError as exc:
            raise WorktreeMintError(
                f"mint mutex unavailable: {exc}",
                retryable=True,
            ) from exc
        try:
            old = _git(wt, "rev-parse", "HEAD")
            if old.returncode != 0 or not old.stdout.strip():
                _refuse("lane HEAD unresolved; omit reuse_thread", ctx=ctx)
            old_tip = old.stdout.strip()
            ctx["old_tip"] = old_tip

            master = _git(repo, "rev-parse", "refs/heads/master")
            if master.returncode != 0 or not master.stdout.strip():
                _refuse("master tip unresolved; omit reuse_thread", ctx=ctx)
            master_sha = master.stdout.strip()

            behind_n = _git(repo, "rev-list", "--count", f"{branch_name}..{master_sha}")
            if behind_n.returncode != 0 or not behind_n.stdout.strip().isdigit():
                _refuse("git rev-list failed; omit reuse_thread", ctx=ctx)
            behind = int(behind_n.stdout.strip())
            ctx["behind"] = behind

            if behind < 1:
                if (
                    old_tip == master_sha
                    and branch_point != master_sha
                    and not sibling_non_terminal_dispatch_on_thread(
                        thread_id=thread_id, exclude_dispatch_id=dispatch_id
                    )
                    and not worktree_is_quarantined(thread_id=thread_id, source_repo=repo)
                ):
                    update_lane_branch_point(
                        source_repo=repo,
                        thread_id=thread_id,
                        branch_point=master_sha,
                    )
                return

            if sibling_non_terminal_dispatch_on_thread(
                thread_id=thread_id, exclude_dispatch_id=dispatch_id
            ):
                _emit_ctx(ctx, "skipped_live_writer")
                return
            if worktree_is_quarantined(thread_id=thread_id, source_repo=repo):
                _refuse("lane worktree is quarantined; omit reuse_thread", ctx=ctx)
            status = _git(wt, "status", "--porcelain", "--untracked-files=no")
            if status.returncode != 0:
                _refuse("git status failed; omit reuse_thread", ctx=ctx)
            if status.stdout.strip():
                _refuse("tracked changes block lane base refresh; omit reuse_thread", ctx=ctx)
            head = _git(wt, "branch", "--show-current")
            if head.returncode != 0 or head.stdout.strip() != branch_name:
                _refuse("lane HEAD is not on the lane branch; omit reuse_thread", ctx=ctx)

            ancestor = _git(repo, "merge-base", "--is-ancestor", branch_name, master_sha)
            if ancestor.returncode not in (0, 1):
                _refuse("merge-base ancestry check failed; omit reuse_thread", ctx=ctx)
            if ancestor.returncode == 0:
                move = _git(wt, "merge", "--ff-only", master_sha)
                outcome = "fast_forwarded"
            else:
                cherry = _git(repo, "cherry", master_sha, branch_name)
                if cherry.returncode != 0:
                    _refuse("git cherry failed; omit reuse_thread", ctx=ctx)
                cherry_lines = [ln.strip() for ln in cherry.stdout.splitlines() if ln.strip()]
                if any(ln.startswith("+ ") for ln in cherry_lines):
                    _emit_ctx(ctx, "left_unlanded")
                    return
                if not cherry_lines:
                    _refuse(
                        "lane not ancestor of master with empty cherry; omit reuse_thread",
                        ctx=ctx,
                    )
                if not all(ln.startswith("- ") for ln in cherry_lines):
                    _refuse("ambiguous cherry output; omit reuse_thread", ctx=ctx)
                if archive_branch(repo=repo, branch_name=branch_name) is None:
                    _refuse("branch archive failed; omit reuse_thread", ctx=ctx)
                move = _git(wt, "reset", "--keep", master_sha)
                outcome = "reset_after_archive"
            if move.returncode != 0:
                _refuse(
                    (move.stderr.strip() or "lane base move failed") + "; omit reuse_thread",
                    ctx=ctx,
                )
            tip = _git(wt, "rev-parse", "HEAD")
            if tip.returncode != 0 or tip.stdout.strip() != master_sha:
                _refuse("post-move HEAD mismatch; omit reuse_thread", ctx=ctx)
            tip_sha = tip.stdout.strip()
            update_lane_branch_point(
                source_repo=repo, thread_id=thread_id, branch_point=tip_sha
            )
            _emit_ctx(ctx, outcome, new_tip=tip_sha)
        finally:
            if mutex_key is not None:
                try:
                    release_mint_mutex(mutex_key=mutex_key, holder_id=dispatch_id)
                except Exception:
                    logger.exception(
                        "release_mint_mutex failed dispatch_id=%s", dispatch_id
                    )
    except (StaleLaneError, WorktreeMintError):
        raise
    except Exception as exc:
        logger.exception("lane base refresh failed thread_id=%s", thread_id)
        _emit_refused_from_ctx(ctx)
        raise StaleLaneError(
            f"lane base refresh failed: {exc}; omit reuse_thread"
        ) from exc


def refresh_lane_after_admit_bind(
    *, source_repo: Path, worktree_path: Path, thread_id: str, dispatch_id: str
) -> None:
    try:
        row = lookup_lane_worktree(thread_id=thread_id, source_repo=source_repo)
        if row is None:
            return
        refresh_landed_lane_base(
            source_repo=source_repo,
            worktree_path=worktree_path,
            branch_name=row.branch_name,
            branch_point=row.branch_point,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
        )
    except (StaleLaneError, WorktreeMintError):
        raise
    except Exception as exc:
        logger.exception("lane base refresh failed thread_id=%s", thread_id)
        _emit(
            "refused",
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            old_tip=None,
            new_tip=None,
            behind=0,
        )
        raise StaleLaneError(
            f"lane base refresh failed: {exc}; omit reuse_thread"
        ) from exc


__all__ = ["StaleLaneError", "refresh_lane_after_admit_bind", "refresh_landed_lane_base"]
