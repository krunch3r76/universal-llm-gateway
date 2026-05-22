"""Shared types, validators, and envelope helpers for worktree operations."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from grokbuild.envelope import _metadata_base

_NAME_VALID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_GIT_TIMEOUT = 30


@dataclass(frozen=True, slots=True)
class WorktreeValidationResult:
    ok: bool
    reason: str = ""
    reason_code: str = ""
    worktree_path: str = ""


def _reject(code: str, reason: str) -> WorktreeValidationResult:
    return WorktreeValidationResult(ok=False, reason_code=code, reason=reason)


def _validate_name(name: str) -> str:
    """Return reason if invalid, empty string if valid."""
    if not name:
        return "name must be non-empty"
    if "/" in name or ".." in name:
        return f"name contains forbidden characters: {name!r}"
    if not _NAME_VALID_RE.match(name):
        return f"name must match {_NAME_VALID_RE.pattern}: {name!r}"
    return ""


def _ref_exists(canonical: str, ref: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", canonical, "rev-parse", "--verify", ref],
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return False
    return True


def _branch_checked_out_worktree(canonical: str, branch: str) -> str:
    """Return path of worktree where `branch` is checked out, else "".

    Uses ``git worktree list --porcelain`` which emits stanzas of
    ``worktree <path>`` / ``branch refs/heads/<name>`` lines per checkout.
    """
    try:
        result = subprocess.run(
            ["git", "-C", canonical, "worktree", "list", "--porcelain"],
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""
    target_ref = f"refs/heads/{branch}"
    current_path = ""
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ").strip()
        elif line.strip() == f"branch {target_ref}":
            return current_path
    return ""


def _worktree_metadata(
    *,
    name: str,
    worktree_path: str,
    branch: str,
    source_repo: str,
    create_branch: bool = False,
    start_point: str = "",
) -> dict[str, Any]:
    meta = _metadata_base(mode="", cwd="", session_id=None, model=None)
    meta.update(
        worktree_name=name,
        worktree_path=worktree_path,
        branch=branch,
        source_repo=source_repo,
        create_branch=create_branch,
        start_point=start_point,
    )
    return meta


def _envelope(
    *,
    dispatch_id: str,
    status: str,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    duration_s: float,
    meta: dict[str, Any],
    reason_code: str = "",
    reason: str = "",
) -> dict[str, Any]:
    meta.update(reason_code=reason_code, reason=reason)
    return {
        "dispatch_id": dispatch_id,
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_s": duration_s,
        "sidecar_path": None,
        "metadata": meta,
    }
