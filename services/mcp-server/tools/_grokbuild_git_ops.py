"""Git push and PR helpers for grokbuild worktrees."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

from tools._grokbuild_envelope import _metadata_base
from tools._grokbuild_worktree import _GIT_TIMEOUT

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True, slots=True)
class GitOpResult:
    ok: bool
    reason_code: str = ""
    reason: str = ""
    branch: str = ""


async def push_op(
    *,
    cwd: str,
    remote: str = "origin",
    branch: str = "",
    set_upstream: bool = True,
) -> dict[str, Any]:
    """Push a worktree branch and report only real git outcomes."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()
    vr = await loop.run_in_executor(None, _validate_git_op, cwd, branch)
    if not vr.ok:
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            cwd=cwd,
            duration_s=0.0,
            reason_code=vr.reason_code,
            reason=vr.reason,
            remote=remote,
            branch=branch,
        )
    if not _valid_name(remote):
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            cwd=cwd,
            duration_s=0.0,
            reason_code="remote_invalid",
            reason=f"remote contains forbidden characters: {remote!r}",
            remote=remote,
            branch=vr.branch,
        )

    cmd = ["git", "-C", cwd, "push"]
    if set_upstream:
        cmd.append("-u")
    cmd.extend([remote, vr.branch])
    proc = await _run_command(cmd)
    upstream = ""
    if proc.returncode == 0:
        upstream = await loop.run_in_executor(None, _current_upstream, cwd)
    return _envelope(
        dispatch_id=dispatch_id,
        status="completed" if proc.returncode == 0 else "failed",
        cwd=cwd,
        duration_s=time.monotonic() - t0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        remote=remote,
        branch=vr.branch,
        upstream=upstream,
        upstream_set=bool(upstream),
    )


async def pr_create_op(
    *,
    cwd: str,
    pr_title: str,
    pr_body: str = "",
    pr_base: str = "",
    pr_head: str = "",
    draft: bool = False,
) -> dict[str, Any]:
    """Create a GitHub PR via gh and return the actual CLI result."""
    dispatch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    loop = asyncio.get_running_loop()
    vr = await loop.run_in_executor(None, _validate_git_op, cwd, "")
    if not vr.ok:
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            cwd=cwd,
            duration_s=0.0,
            reason_code=vr.reason_code,
            reason=vr.reason,
            pr_title=pr_title,
        )
    if not pr_title.strip():
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            cwd=cwd,
            duration_s=0.0,
            reason_code="pr_title_missing",
            reason="pr_title is required",
        )
    if shutil.which("gh") is None:
        return _envelope(
            dispatch_id=dispatch_id,
            status="rejected",
            cwd=cwd,
            duration_s=0.0,
            reason_code="gh_not_in_path",
            reason="gh executable not found on PATH",
            pr_title=pr_title,
        )

    cmd = ["gh", "pr", "create", "--title", pr_title, "--body", pr_body]
    if pr_base:
        cmd.extend(["--base", pr_base])
    if pr_head:
        cmd.extend(["--head", pr_head])
    if draft:
        cmd.append("--draft")
    proc = await _run_command(cmd, cwd=cwd)
    return _envelope(
        dispatch_id=dispatch_id,
        status="completed" if proc.returncode == 0 else "failed",
        cwd=cwd,
        duration_s=time.monotonic() - t0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        exit_code=proc.returncode,
        branch=vr.branch,
        pr_title=pr_title,
        pr_body=pr_body,
        pr_base=pr_base,
        pr_head=pr_head,
        draft=draft,
        pr_url=proc.stdout.strip().splitlines()[-1] if proc.returncode == 0 else "",
    )


def _validate_git_op(cwd: str, branch: str) -> GitOpResult:
    if not cwd or not os.path.isabs(cwd) or not os.path.isdir(cwd):
        return GitOpResult(False, "cwd_missing", f"cwd must exist: {cwd!r}")
    if branch and not _valid_name(branch):
        return GitOpResult(False, "branch_invalid", f"invalid branch: {branch!r}")
    try:
        subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return GitOpResult(False, "not_a_git_repo", f"cwd is not a git repo: {cwd!r}")
    resolved = branch or _current_branch(cwd)
    if not resolved:
        return GitOpResult(False, "branch_missing", "branch required in detached HEAD")
    return GitOpResult(True, branch=resolved)


def _valid_name(value: str) -> bool:
    return bool(value) and bool(_NAME_RE.fullmatch(value))


def _current_branch(cwd: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip()


def _current_upstream(cwd: str) -> str:
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                cwd,
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{u}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip()


async def _run_command(
    cmd: list[str], cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode or 0,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
    )


def _envelope(
    *,
    dispatch_id: str,
    status: str,
    cwd: str,
    duration_s: float,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    reason_code: str = "",
    reason: str = "",
    **fields: Any,
) -> dict[str, Any]:
    meta = _metadata_base(mode="", cwd=cwd, session_id=None, model=None)
    meta.update(reason_code=reason_code, reason=reason, **fields)
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
