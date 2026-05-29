"""Admission checks for integrate_op.

All checks are synchronous (run in run_in_executor from the async caller) so
they can call subprocess without blocking the event loop.
"""

from __future__ import annotations

import os
import subprocess

from universal_logging import get_logger

from git_integrate.git_cas import diff_sha256
from git_integrate.schema import (
    RC_APPROVAL_MISSING,
    RC_ARC_BRANCH_MISMATCH,
    RC_DIFF_MISMATCH,
    RC_NOT_A_GIT_REPO,
    RC_WORKTREE_MISSING,
    IntegrateResult,
)

_GIT_TIMEOUT = 10.0
_logger = get_logger(__name__)


def _reject(reason_code: str, reason: str) -> IntegrateResult:
    return IntegrateResult(ok=False, reason_code=reason_code, reason=reason)


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
      4. diff_sha256 matches expected (post-approval mutation guard)
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

    # Post-approval mutation guard: re-verify the diff has not changed since
    # the operator reviewed it (abuse-vector b: worktree mutated after approval).
    actual_sha = diff_sha256(worktree_path)
    if actual_sha != expected_diff_sha256:
        return _reject(
            RC_DIFF_MISMATCH,
            f"diff changed since approval: "
            f"got {actual_sha[:16]}..., expected {expected_diff_sha256[:16]}...",
        )

    return IntegrateResult(ok=True)
