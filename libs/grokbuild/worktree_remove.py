"""``worktree_remove_op`` handler — git worktree removal with busy/dirty guards.

Shared constants (``WORKTREE_ROOT``, ``_GIT_TIMEOUT``) are accessed via
module-attribute lookup on ``grokbuild.worktree`` so test monkeypatches at
that path propagate here. Other helpers (``_validate_name``, ``_envelope``,
``_worktree_metadata``, ``WorktreeValidationResult``, ``_reject``) are
imported by name — they are pure functions and don't get patched.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import time
import uuid
from typing import Any

import grokbuild.worktree as _wt
from grokbuild.events import (
    emit_grok_build_remove_called,
    emit_grok_build_remove_completed,
    emit_grok_build_remove_failed,
    emit_grok_build_remove_rejected,
)
from grokbuild.registry import cwds_under
from grokbuild.worktree import (
    WorktreeValidationResult,
    _envelope,
    _reject,
    _validate_name,
    _worktree_metadata,
)


def validate_worktree_remove(name: str) -> WorktreeValidationResult:
    """Admission checks (synchronous) for worktree_remove.

    The ``worktree_busy`` check (registry lookup) is async and runs in the
    handler after this validator passes.
    """
    name_err = _validate_name(name)
    if name_err:
        return _reject("name_invalid", name_err)
    target = os.path.join(_wt.WORKTREE_ROOT, name)
    if not os.path.isdir(target):
        return _reject("worktree_not_found", f"worktree path does not exist: {target}")
    try:
        proc = subprocess.run(
            ["git", "-C", target, "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_wt._GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return _reject(
            "worktree_not_found", f"path is not a valid git worktree: {target}"
        )
    if proc.stdout.strip():
        return _reject(
            "worktree_dirty",
            f"uncommitted changes in worktree (commit or stash first): {target}",
        )
    return WorktreeValidationResult(ok=True, worktree_path=target)


async def worktree_remove_op(*, name: str) -> dict[str, Any]:
    """Remove a worktree under ``WORKTREE_ROOT/<name>``.

    Refuses if a dispatch is in flight against any cwd under the worktree
    (``worktree_busy``) or if the worktree has uncommitted changes
    (``worktree_dirty``). No ``--force`` escape hatch — caller must resolve
    dirty state explicitly.

    Event-emit ordering matches ``grokbuild.dispatch``: ``.called`` fires
    AFTER admission passes (rejected removes emit only ``.rejected``).
    Aligned across worktree ops in V2 close-out (review W9).
    """
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()

    loop = asyncio.get_running_loop()
    vr = await loop.run_in_executor(None, validate_worktree_remove, name)
    if not vr.ok:
        return _remove_rejected(
            dispatch_id=dispatch_id,
            name=name,
            vr=vr,
            reason_code=vr.reason_code,
            reason=vr.reason,
        )

    in_flight = await cwds_under(vr.worktree_path)
    if in_flight:
        reason = f"worktree path has in-flight dispatch(es): {sorted(in_flight)!r}"
        return _remove_rejected(
            dispatch_id=dispatch_id,
            name=name,
            vr=vr,
            reason_code="worktree_busy",
            reason=reason,
        )

    emit_grok_build_remove_called(dispatch_id=dispatch_id, name=name)

    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        vr.worktree_path,
        "worktree",
        "remove",
        vr.worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=_wt._GIT_TIMEOUT
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        duration_s = time.monotonic() - t0
        emit_grok_build_remove_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=None,
            error=f"git worktree remove timed out after {_wt._GIT_TIMEOUT}s",
            name=name,
            worktree_path=vr.worktree_path,
        )
        meta = _worktree_metadata(
            name=name, worktree_path=vr.worktree_path, branch="", source_repo=""
        )
        return _envelope(
            dispatch_id=dispatch_id,
            status="failed",
            stdout="",
            stderr=f"git worktree remove timed out after {_wt._GIT_TIMEOUT}s",
            exit_code=None,
            duration_s=duration_s,
            meta=meta,
        )
    duration_s = time.monotonic() - t0
    stdout = stdout_b.decode(errors="replace")
    stderr = stderr_b.decode(errors="replace")

    if proc.returncode == 0:
        emit_grok_build_remove_completed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=0,
            name=name,
            worktree_path=vr.worktree_path,
        )
        status = "completed"
    else:
        emit_grok_build_remove_failed(
            dispatch_id=dispatch_id,
            duration_s=duration_s,
            exit_code=proc.returncode,
            error=stderr[:200],
            name=name,
            worktree_path=vr.worktree_path,
        )
        status = "failed"
    meta = _worktree_metadata(
        name=name, worktree_path=vr.worktree_path, branch="", source_repo=""
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        duration_s=duration_s,
        meta=meta,
    )


def _remove_rejected(
    *,
    dispatch_id: str,
    name: str,
    vr: WorktreeValidationResult,
    reason_code: str,
    reason: str,
) -> dict[str, Any]:
    emit_grok_build_remove_rejected(
        dispatch_id=dispatch_id,
        reason_code=reason_code,
        reason=reason,
        name=name,
        worktree_path=vr.worktree_path,
    )
    meta = _worktree_metadata(
        name=name, worktree_path=vr.worktree_path, branch="", source_repo=""
    )
    return _envelope(
        dispatch_id=dispatch_id,
        status="rejected",
        stdout="",
        stderr="",
        exit_code=None,
        duration_s=0.0,
        meta=meta,
        reason_code=reason_code,
        reason=reason,
    )
