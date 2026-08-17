"""SDK-reported git branch extraction and the day-1 FS/git cross-check.

``extract_sdk_git_snapshot`` reads the first SDK git branch record.
``sdk_fs_git_mismatch_reason`` compares that branch to ``git branch
--show-current`` on the source repo (``_git_branch_name``). ``subprocess``
here is a distinct patch target from ``worktree_baseline.subprocess`` and
``lint_verification.subprocess``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def extract_sdk_git_snapshot(git_info: Any) -> dict[str, Any] | None:
    if git_info is None:
        return None
    branches = getattr(git_info, "branches", None) or ()
    if not branches:
        return None
    first = branches[0]
    return {
        "repo_url": getattr(first, "repo_url", None),
        "branch": getattr(first, "branch", None),
        "pr_url": getattr(first, "pr_url", None),
    }


def _git_branch_name(source_repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "branch", "--show-current"],
            capture_output=True,
            check=True,
            timeout=5,
            text=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    branch = proc.stdout.strip()
    return branch or None


def sdk_fs_git_mismatch_reason(
    sdk_git: dict[str, Any] | None,
    source_repo: Path,
) -> str | None:
    """Return ``sdk_fs_mismatch`` when SDK git disagrees with FS/git (day-1 XCHECK)."""
    if not sdk_git:
        return None
    fs_branch = _git_branch_name(source_repo)
    sdk_branch = sdk_git.get("branch")
    if fs_branch and sdk_branch and fs_branch != sdk_branch:
        return "sdk_fs_mismatch"
    return None
