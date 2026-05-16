"""Worktree shared helpers + ``worktree_create_op`` handler.

The ``worktree_remove`` op lives in ``_grok_build_worktree_remove`` to keep
each module under the 300-SLOC ceiling; shared constants and helpers live
here and are imported via module-attribute access so monkeypatching
``WORKTREE_ROOT`` / ``ALLOWED_SOURCE_ROOT`` in tests propagates to both
handlers.

Worktrees are rooted at ``WORKTREE_ROOT`` under ``/mnt/torus/projects/...``
to satisfy the MCP container's bind-mount visibility constraint (see
``SKILL.md`` §3.4 / case study). Caller passes ``name``; validator
constructs the absolute path. No override path — operator escape hatch is
``git worktree add`` + ``op="dispatch"`` with explicit ``cwd``.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

from tools._grok_build_envelope import _metadata_base
from tools._grok_build_events import (
    emit_grok_build_create_called,
    emit_grok_build_create_completed,
    emit_grok_build_create_failed,
    emit_grok_build_create_rejected,
)

WORKTREE_ROOT = "/mnt/torus/projects/ulg-grok-worktrees"
ALLOWED_SOURCE_ROOT = "/mnt/torus/projects"
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


def _worktree_metadata(
    *, name: str, worktree_path: str, branch: str, source_repo: str
) -> dict[str, Any]:
    meta = _metadata_base(mode="", cwd="", session_id=None, model=None)
    meta.update(
        worktree_name=name,
        worktree_path=worktree_path,
        branch=branch,
        source_repo=source_repo,
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


def _validate_source_repo(source_repo: str) -> tuple[str, str]:
    """Return (canonical_source, reason). reason is empty if valid."""
    if not source_repo:
        return "", "source_repo is required"
    canonical = os.path.realpath(source_repo)
    allowed = os.path.realpath(ALLOWED_SOURCE_ROOT)
    if not (canonical == allowed or canonical.startswith(allowed + os.sep)):
        return "", f"source_repo must be under {ALLOWED_SOURCE_ROOT}: {source_repo!r}"
    if not os.path.isdir(canonical):
        return "", f"source_repo does not exist: {source_repo!r}"
    try:
        subprocess.run(
            ["git", "-C", canonical, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return "", f"source_repo is not a git repository: {source_repo!r}"
    return canonical, ""


def validate_worktree_create(
    name: str, branch: str, source_repo: str
) -> WorktreeValidationResult:
    """Admission checks for worktree_create. Short-circuit on first failure."""
    name_err = _validate_name(name)
    if name_err:
        return _reject("name_invalid", name_err)
    if not branch:
        return _reject("name_invalid", "branch is required")
    canonical, source_err = _validate_source_repo(source_repo)
    if source_err:
        return _reject("source_repo_invalid", source_err)
    try:
        subprocess.run(
            ["git", "-C", canonical, "rev-parse", "--verify", branch],
            check=True,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(
            "branch_not_found", f"branch not found in source_repo: {branch!r}"
        )
    target = os.path.join(WORKTREE_ROOT, name)
    if os.path.exists(target):
        return _reject(
            "worktree_exists", f"target worktree path already exists: {target}"
        )
    return WorktreeValidationResult(ok=True, worktree_path=target)


def _ensure_worktree_root() -> None:
    os.makedirs(WORKTREE_ROOT, exist_ok=True)


async def worktree_create_op(
    *, name: str, branch: str, source_repo: str
) -> dict[str, Any]:
    """Create a git worktree under ``WORKTREE_ROOT/<name>`` at ``branch``."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    emit_grok_build_create_called(
        dispatch_id=dispatch_id, name=name, branch=branch, source_repo=source_repo
    )

    loop = asyncio.get_running_loop()
    vr = await loop.run_in_executor(
        None, validate_worktree_create, name, branch, source_repo
    )
    if not vr.ok:
        emit_grok_build_create_rejected(
            dispatch_id=dispatch_id,
            reason_code=vr.reason_code,
            reason=vr.reason,
            name=name,
            branch=branch,
            source_repo=source_repo,
        )
        meta = _worktree_metadata(
            name=name,
            worktree_path=vr.worktree_path,
            branch=branch,
            source_repo=source_repo,
        )
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=0.0,
            meta=meta,
            reason_code=vr.reason_code,
            reason=vr.reason,
        )

    try:
        await loop.run_in_executor(None, _ensure_worktree_root)
    except OSError as exc:
        return _emit_create_failed(
            dispatch_id=dispatch_id,
            t0=t0,
            exit_code=None,
            stderr=str(exc),
            name=name,
            branch=branch,
            source_repo=source_repo,
            worktree_path=vr.worktree_path,
        )

    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        source_repo,
        "worktree",
        "add",
        vr.worktree_path,
        branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    duration_s = time.monotonic() - t0
    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")

    meta = _worktree_metadata(
        name=name,
        worktree_path=vr.worktree_path,
        branch=branch,
        source_repo=source_repo,
    )
    if proc.returncode == 0:
        emit_grok_build_create_completed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=0,
            name=name,
            branch=branch,
            source_repo=source_repo,
            worktree_path=vr.worktree_path,
        )
        return _envelope(
            dispatch_id=dispatch_id,
            status="completed",
            stdout=stdout,
            stderr=stderr,
            exit_code=0,
            duration_s=duration_s,
            meta=meta,
        )
    emit_grok_build_create_failed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        exit_code=proc.returncode,
        error=stderr[:200],
        name=name,
        branch=branch,
        source_repo=source_repo,
        worktree_path=vr.worktree_path,
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="failed",
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        duration_s=duration_s,
        meta=meta,
    )


def _emit_create_failed(
    *,
    dispatch_id: str,
    t0: float,
    exit_code: int | None,
    stderr: str,
    name: str,
    branch: str,
    source_repo: str,
    worktree_path: str,
) -> dict[str, Any]:
    duration_s = time.monotonic() - t0
    emit_grok_build_create_failed(
        dispatch_id=dispatch_id,
        duration_s=duration_s,
        exit_code=exit_code,
        error=stderr[:200],
        name=name,
        branch=branch,
        source_repo=source_repo,
        worktree_path=worktree_path,
    )
    meta = _worktree_metadata(
        name=name,
        worktree_path=worktree_path,
        branch=branch,
        source_repo=source_repo,
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="failed",
        stdout="",
        stderr=stderr,
        exit_code=exit_code,
        duration_s=duration_s,
        meta=meta,
    )
