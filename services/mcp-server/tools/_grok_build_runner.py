"""Async subprocess runner for grok_build dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

STDOUT_MAX = 64 * 1024
STDERR_MAX = 16 * 1024
_SIDECAR_DIR = Path("/tmp/logs/grok-build")

_READ_ONLY_PREFIX = (
    "MODE: read_only. The operator has invoked you in advisory mode. "
    "Do NOT modify, create, or delete files. Do NOT run shell commands "
    "that mutate the filesystem (no `git commit`, `git add`, `mv`, `rm`, "
    "no editor saves, no code execution that writes outputs). Instead, "
    "narrate the changes you would propose — describe the diff in prose, "
    "name the files you would touch, and quote the exact patch hunks. "
    "The operator will review your proposal and re-invoke you in edit "
    "mode if they want the changes applied. If the task requires writing "
    "to proceed (e.g., the user asked you to create a file), refuse and "
    "explain that this mode does not permit writes."
)

_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL")


@dataclass(frozen=True, slots=True)
class RunnerSpec:
    dispatch_id: str
    cwd: str
    prompt: str
    mode: Literal["read_only", "edit"]
    permission_mode: str
    system_context: str | None
    model: str | None
    session_id: str | None
    continue_recent: bool
    output_format: Literal["json", "streaming-json"]
    timeout_seconds: int
    grok_path: str
    git_status_pre: str


@dataclass(frozen=True, slots=True)
class RunnerResult:
    status: Literal["completed", "failed", "timeout"]
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    sidecar_path: str | None
    truncated: bool
    git_status_post: str
    git_diff_stat: str
    audit_incomplete: bool = False
    sidecar_gaps: int = 0
    error: str = ""


def _build_env() -> dict[str, str]:
    src = os.environ
    env = {k: src[k] for k in _ALLOW if k in src}
    env["TERM"] = "dumb"
    return env


def _build_argv(spec: RunnerSpec) -> list[str]:
    read_only_rules = _READ_ONLY_PREFIX if spec.mode == "read_only" else ""
    combined_rules = "\n\n".join(
        part for part in (read_only_rules, spec.system_context or "") if part
    )
    argv = [
        spec.grok_path,
        "-p",
        spec.prompt,
        "--cwd",
        spec.cwd,
        "--output-format",
        spec.output_format,
        "--permission-mode",
        spec.permission_mode,
        "--always-approve",
    ]
    if spec.model:
        argv.extend(["--model", spec.model])
    if combined_rules:
        argv.extend(["--rules", combined_rules])
    if spec.session_id:
        argv.extend(["--resume", spec.session_id])
    elif spec.continue_recent:
        argv.append("--continue")
    return argv


def _truncate_tail(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    if truncated:
        data = data[-limit:]
    return data.decode(errors="replace"), truncated


async def _capture_post_state(cwd: str) -> tuple[str, str, bool]:
    """Capture post-dispatch git state.

    Returns (status_porcelain, diff_stat, audit_incomplete). audit_incomplete
    is True when a git invocation failed (timeout, non-zero exit, OS error) —
    callers MUST treat a True flag as "do not trust the verdict for this
    dispatch", distinct from a clean repo (status="") which is a TRUE clean
    signal.
    """
    loop = asyncio.get_running_loop()

    def _do_capture() -> tuple[str, str, bool]:
        try:
            status_proc = subprocess.run(
                ["git", "-C", cwd, "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return "", "", True
        status = status_proc.stdout
        diff = ""
        if status.strip():
            try:
                diff_proc = subprocess.run(
                    ["git", "-C", cwd, "diff", "--stat"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                # status read succeeded; diff failed — treat verdict as suspect.
                return status, "", True
            diff = diff_proc.stdout
        return status, diff, False

    return await loop.run_in_executor(None, _do_capture)


def _sidecar_path(dispatch_id: str) -> str:
    return str(_SIDECAR_DIR / f"{dispatch_id}.ndjson")


def _append_sidecar(path: str, record: dict[str, object]) -> None:
    line = json.dumps(record, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def _try_append_sidecar(path: str, record: dict[str, object], gaps: list[int]) -> None:
    """Append to sidecar; on OSError, increment the shared gaps counter.

    The counter is propagated to the terminal RunnerResult so audit consumers
    can detect partial sidecars (vs silently swallowing OSError).
    """
    try:
        _append_sidecar(path, record)
    except OSError:
        gaps[0] += 1


async def run_dispatch(spec: RunnerSpec) -> RunnerResult:
    """Spawn grok, capture output, sidecar, and post-invocation git state."""
    t0 = time.monotonic()
    argv = _build_argv(spec)
    sidecar = _sidecar_path(spec.dispatch_id)
    gaps: list[int] = [0]

    try:
        _append_sidecar(
            sidecar,
            {
                "phase": "started",
                "ts": int(time.time() * 1000),
                "argv": argv,
                "env_keys": sorted(_build_env()),
                "git_status_pre": spec.git_status_pre,
            },
        )
    except OSError as exc:
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=time.monotonic() - t0,
            sidecar_path=None,
            truncated=False,
            git_status_post="",
            git_diff_stat="",
            audit_incomplete=True,
            sidecar_gaps=0,
            error=f"sidecar_write_failed: {exc}",
        )

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=spec.cwd,
        env=_build_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=spec.timeout_seconds,
        )
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, TimeoutError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
            spec.cwd
        )
        duration_s = time.monotonic() - t0
        _try_append_sidecar(
            sidecar,
            {
                "phase": "exit",
                "ts": int(time.time() * 1000),
                "exit_code": None,
                "git_status_post": git_status_post,
                "git_diff_stat": git_diff_stat,
                "audit_incomplete": audit_incomplete,
            },
            gaps,
        )
        return RunnerResult(
            status="timeout",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=duration_s,
            sidecar_path=sidecar,
            truncated=False,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=gaps[0],
        )

    if spec.output_format == "streaming-json":
        for line in stdout_b.splitlines():
            _try_append_sidecar(
                sidecar,
                {
                    "phase": "stdout_chunk",
                    "ts": int(time.time() * 1000),
                    "data": line.decode(errors="replace"),
                },
                gaps,
            )
    else:
        _try_append_sidecar(
            sidecar,
            {
                "phase": "stdout_chunk",
                "ts": int(time.time() * 1000),
                "data": stdout_b.decode(errors="replace"),
            },
            gaps,
        )

    if stderr_b:
        _try_append_sidecar(
            sidecar,
            {
                "phase": "stderr_chunk",
                "ts": int(time.time() * 1000),
                "data": stderr_b.decode(errors="replace"),
            },
            gaps,
        )

    git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
        spec.cwd
    )
    exit_code = proc.returncode
    duration_s = time.monotonic() - t0

    _try_append_sidecar(
        sidecar,
        {
            "phase": "exit",
            "ts": int(time.time() * 1000),
            "exit_code": exit_code,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "audit_incomplete": audit_incomplete,
        },
        gaps,
    )

    stdout, truncated = _truncate_tail(stdout_b, STDOUT_MAX)
    stderr, _ = _truncate_tail(stderr_b, STDERR_MAX)
    status: Literal["completed", "failed"] = "completed" if exit_code == 0 else "failed"
    return RunnerResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_s=duration_s,
        sidecar_path=sidecar,
        truncated=truncated,
        git_status_post=git_status_post,
        git_diff_stat=git_diff_stat,
        audit_incomplete=audit_incomplete,
        sidecar_gaps=gaps[0],
    )
