"""Admission checks for integrate_op.

All checks are synchronous (run in run_in_executor from the async caller) so
they can call subprocess without blocking the event loop.
"""

from __future__ import annotations

import os
import subprocess

from universal_logging import get_logger

from git_integrate.commit_paths import commit_paths_fingerprint
from git_integrate.git_cas import diff_sha256, is_dirty, land_fingerprint
from git_integrate.schema import (
    EMPTY_DIFF_SHA256,
    RC_APPROVAL_MISSING,
    RC_ARC_BRANCH_MISMATCH,
    RC_BRANCH_MISMATCH,
    RC_DIFF_MISMATCH,
    RC_DIRTY_WORKTREE,
    RC_NO_CHANGES_FOR_PATHS,
    RC_NOT_A_GIT_REPO,
    RC_NOTHING_TO_LAND,
    RC_PATHS_EMPTY,
    RC_UNCOMMITTED_NO_MESSAGE,
    RC_WORKTREE_MISSING,
    IntegrateResult,
)

_GIT_TIMEOUT = 10.0
_logger = get_logger(__name__)


def _reject(reason_code: str, reason: str) -> IntegrateResult:
    return IntegrateResult(ok=False, reason_code=reason_code, reason=reason)


def _base_checks(
    *,
    arc: str,
    worktree_path: str,
    approval: str,
) -> IntegrateResult | None:
    """Shared admission checks; returns IntegrateResult on failure, None if ok."""
    if not os.path.isdir(worktree_path):
        return _reject(
            RC_WORKTREE_MISSING, f"worktree does not exist: {worktree_path!r}"
        )

    try:
        subprocess.run(
            ["git", "-C", worktree_path, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(RC_NOT_A_GIT_REPO, f"not a git repo: {worktree_path!r}")

    try:
        branch_proc = subprocess.run(
            ["git", "-C", worktree_path, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(RC_ARC_BRANCH_MISMATCH, "failed to read current branch")

    current_branch = branch_proc.stdout.strip()
    expected_branch = f"arc/{arc}"
    if current_branch != expected_branch:
        return _reject(
            RC_ARC_BRANCH_MISMATCH,
            f"branch is {current_branch!r}; expected {expected_branch!r}",
        )

    if not approval:
        return _reject(RC_APPROVAL_MISSING, "approval token is required")

    return None


def validate_integrate(
    *,
    arc: str,
    worktree_path: str,
    approval: str,
    expected_diff_sha256: str,
) -> IntegrateResult:
    """Synchronous admission checks — short-circuit on first failure.

    Order:
      1. worktree_path exists and is a git repo
      2. current branch is arc/<arc>
      3. approval is non-empty
      4. reject dirty worktrees (P0 data-loss guard)
      5. diff_sha256 matches expected (post-approval mutation guard)
    """
    base = _base_checks(arc=arc, worktree_path=worktree_path, approval=approval)
    if base is not None:
        return base

    if is_dirty(worktree_path):
        return _reject(
            RC_DIRTY_WORKTREE,
            "uncommitted changes present; use land with a commit_message, or commit first",
        )

    actual_sha = diff_sha256(worktree_path)
    if actual_sha != expected_diff_sha256:
        return _reject(
            RC_DIFF_MISMATCH,
            f"diff changed since approval: "
            f"got {actual_sha[:16]}..., expected {expected_diff_sha256[:16]}...",
        )

    return IntegrateResult(ok=True)


def validate_land(
    *,
    arc: str,
    worktree_path: str,
    approval: str,
    expected_diff_sha256: str,
    commit_message: str,
    dirty: bool,
) -> IntegrateResult:
    """Admission checks for land_op — dirty-aware fingerprint binding."""
    base = _base_checks(arc=arc, worktree_path=worktree_path, approval=approval)
    if base is not None:
        return base

    fingerprint = land_fingerprint(worktree_path)
    if fingerprint == EMPTY_DIFF_SHA256:
        return _reject(RC_NOTHING_TO_LAND, "nothing to land — empty diff")

    if fingerprint != expected_diff_sha256:
        return _reject(
            RC_DIFF_MISMATCH,
            f"diff changed since approval: "
            f"got {fingerprint[:16]}..., expected {expected_diff_sha256[:16]}...",
        )

    if dirty and not commit_message:
        return _reject(
            RC_UNCOMMITTED_NO_MESSAGE,
            "commit_message required when worktree has uncommitted changes",
        )

    return IntegrateResult(ok=True)


def validate_commit(
    *,
    worktree_path: str,
    expected_branch: str,
    paths: list[str],
    approval: str,
    expected_paths_sha256: str,
    commit_message: str,
) -> IntegrateResult:
    """Admission checks for commit_op — path-scoped, branch-affirmed, fingerprint-bound.

    Order:
      1. worktree_path exists and is a git repo
      2. approval non-empty, paths non-empty, commit_message non-empty
      3. current branch == expected_branch (affirm intended target)
      4. path-scoped fingerprint non-empty and == expected_paths_sha256
    """
    if not os.path.isdir(worktree_path):
        return _reject(
            RC_WORKTREE_MISSING, f"worktree does not exist: {worktree_path!r}"
        )
    try:
        subprocess.run(
            ["git", "-C", worktree_path, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(RC_NOT_A_GIT_REPO, f"not a git repo: {worktree_path!r}")

    if not approval:
        return _reject(RC_APPROVAL_MISSING, "approval token is required")
    if not paths:
        return _reject(RC_PATHS_EMPTY, "at least one path is required")
    if not commit_message:
        return _reject(RC_UNCOMMITTED_NO_MESSAGE, "commit_message is required")

    try:
        branch_proc = subprocess.run(
            ["git", "-C", worktree_path, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(RC_BRANCH_MISMATCH, "failed to read current branch")
    current_branch = branch_proc.stdout.strip()
    if current_branch != expected_branch:
        return _reject(
            RC_BRANCH_MISMATCH,
            f"branch is {current_branch!r}; expected {expected_branch!r}",
        )

    fingerprint = commit_paths_fingerprint(worktree_path, paths)
    if not fingerprint or fingerprint == EMPTY_DIFF_SHA256:
        return _reject(RC_NO_CHANGES_FOR_PATHS, "no staged changes for the named paths")
    if fingerprint != expected_paths_sha256:
        return _reject(
            RC_DIFF_MISMATCH,
            f"paths changed since approval: "
            f"got {fingerprint[:16]}..., expected {expected_paths_sha256[:16]}...",
        )

    return IntegrateResult(ok=True)
