"""Async subprocess runner for grokbuild dispatch (V1).

Path classification and reason_code values are documented on
``run_dispatch``. The runner sees fully-resolved scalars only — tier
overlays and mode-aware defaults are applied by ``grokbuild.dispatch``
before this module is invoked.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Literal

from grokbuild.constants import _SIDECAR_DIR
from grokbuild.runner_argv import (  # noqa: F401 — re-exported
    _ALLOW,
    _OVERRIDE,
    _READ_ONLY_PREFIX,
    _build_argv,
    _build_env,
)
from grokbuild.runner_home import preflight_inspect, setup_dispatch_home
from grokbuild.runner_sidecar import (  # noqa: F401 — re-exported; patching "grokbuild.runner.*" in tests works via this namespace
    _append_sidecar,
    _capture_post_state,
    _snap_session_id,
    _try_append_sidecar,
    _try_append_sidecar_chunk,
)
from grokbuild.runner_types import (  # noqa: F401 — re-exported
    SIDECAR_STDERR_BYTE_MAX,
    SIDECAR_STDOUT_LINE_MAX,
    STDERR_MAX,
    STDOUT_MAX,
    RunnerResult,
    RunnerSpec,
)

# _SIDECAR_DIR is canonically defined in constants (review W8);
# the import above preserves `grokbuild.runner._SIDECAR_DIR`
# as a monkey-patch target for test fixtures.


def _truncate_tail(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    if truncated:
        data = data[-limit:]
    return data.decode(errors="replace"), truncated


def _sidecar_path(dispatch_id: str) -> str:
    return str(_SIDECAR_DIR / f"{dispatch_id}.ndjson")


async def run_dispatch(spec: RunnerSpec) -> RunnerResult:
    """Spawn grok, capture output, sidecar, and post-invocation git state.

    Path classification:
        * sidecar_write_failed → status='failed', reason_code='sidecar_unwritable'
        * subprocess spawn fails (OSError, FileNotFoundError, PermissionError)
          → status='failed', reason_code='spawn_failed'; NO process to wait on
        * grok exits cleanly with non-zero → status='failed', reason_code='grok_nonzero_exit'
        * timeout fires → status='timeout' (no reason_code)
        * grok exits 0 → status='completed'
    """
    t0 = time.monotonic()
    argv = _build_argv(spec)
    sidecar = _sidecar_path(spec.dispatch_id)
    gaps: list[int] = [0]

    _env = _build_env()
    if spec.recursion_depth is not None:
        _env["GROKBUILD_RECURSION_DEPTH"] = str(spec.recursion_depth)

    # Per-dispatch HOME override (seat-promotion, item 7-10).
    # HOME is in _OVERRIDE (not _ALLOW), so it must be explicitly set here.
    # When MCP_GROK_BUILD_DISPATCH_TOKEN is configured:
    #   - create <sidecar_dir>/<dispatch_id>-home/ with dispatch config.toml
    #   - run grok inspect --json to verify the override took effect
    #   - refuse dispatch on pre-flight failure (prevents silent attribution drift)
    # When token is not configured: fall back to inherited HOME with a warning.
    _dispatch_token = os.getenv("MCP_GROK_BUILD_DISPATCH_TOKEN", "").strip()
    _real_home = os.environ.get("HOME", "")
    if _dispatch_token:
        try:
            _dispatch_home = setup_dispatch_home(
                spec.dispatch_id,
                _SIDECAR_DIR,
                token=_dispatch_token,
                real_home=_real_home,
            )
        except OSError as _exc:
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
                error=f"dispatch_home_setup_failed: {_exc}",
                reason_code="dispatch_home_setup_failed",
                dirty_admission=spec.dirty_admission,
            )
        _env["HOME"] = str(_dispatch_home)
        # Pre-flight assertion (item 10): verify grok inspect sees override config.
        _preflight_error = preflight_inspect(
            spec.dispatch_id, _dispatch_home, spec.grok_path, _env
        )
        if _preflight_error:
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
                error=f"dispatch_preflight_failed: {_preflight_error}",
                reason_code="dispatch_preflight_failed",
                dirty_admission=spec.dirty_admission,
            )
    else:
        # Token not configured — fall back to real HOME with warning so
        # existing environments continue to work during transition.
        if _real_home:
            _env["HOME"] = _real_home

    try:
        _append_sidecar(
            sidecar,
            {
                "phase": "started",
                "ts": int(time.time() * 1000),
                "argv": argv,
                "env_keys": sorted(_env),
                "cwd": spec.cwd,
                "mode": spec.mode,
                "permission_mode": spec.permission_mode,
                "model": spec.model,
                "session_id": spec.session_id,
                "resume_strict": spec.resume_strict,
                "tier": spec.tier,
                "reasoning_effort": spec.reasoning_effort,
                "effort": spec.effort,
                "check": spec.check,
                "no_subagents": spec.no_subagents,
                "disable_web_search": spec.disable_web_search,
                "max_turns": spec.max_turns,
                "best_of_n": spec.best_of_n,
                "output_format": "streaming-json",
                "git_status_pre": spec.git_status_pre,
                "dirty_admission": spec.dirty_admission,
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
            reason_code="sidecar_unwritable",
            dirty_admission=spec.dirty_admission,
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=spec.cwd,
            env=_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        if spec.proc_pid_holder is not None:
            spec.proc_pid_holder.append(-1)
        # FileNotFoundError and PermissionError are subclasses of OSError — the
        # parent catches them too. Naming kept descriptive in the error string.
        duration_s = time.monotonic() - t0
        _try_append_sidecar(
            sidecar,
            {
                "phase": "exit",
                "ts": int(time.time() * 1000),
                "status": "failed",
                "exit_code": None,
                "duration_s": duration_s,
                "error": f"spawn_failed: {exc}",
                "reason_code": "spawn_failed",
            },
            gaps,
        )
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=None,
            duration_s=duration_s,
            sidecar_path=sidecar,
            truncated=False,
            git_status_post="",
            git_diff_stat="",
            audit_incomplete=True,
            sidecar_gaps=gaps[0],
            error=f"spawn_failed: {exc}",
            reason_code="spawn_failed",
            dirty_admission=spec.dirty_admission,
        )

    if spec.proc_pid_holder is not None:
        spec.proc_pid_holder.append(proc.pid)

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
                "status": "timeout",
                "exit_code": None,
                "duration_s": duration_s,
                "git_status_post": git_status_post,
                "git_diff_stat": git_diff_stat,
                "audit_incomplete": audit_incomplete,
                "sidecar_gaps": gaps[0],
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
            dirty_admission=spec.dirty_admission,
        )
    except OSError as exc:
        # Broken pipe, EBADF, or other I/O failure mid-communicate (review W4).
        # Honor the structured failure envelope contract instead of letting the
        # exception propagate to the MCP error path.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
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
                "status": "failed",
                "exit_code": proc.returncode,
                "duration_s": duration_s,
                "git_status_post": git_status_post,
                "git_diff_stat": git_diff_stat,
                "audit_incomplete": audit_incomplete,
                "sidecar_gaps": gaps[0],
                "error": f"communicate_failed: {exc}",
                "reason_code": "communicate_failed",
            },
            gaps,
        )
        return RunnerResult(
            status="failed",
            stdout="",
            stderr="",
            exit_code=proc.returncode,
            duration_s=duration_s,
            sidecar_path=sidecar,
            truncated=False,
            git_status_post=git_status_post,
            git_diff_stat=git_diff_stat,
            audit_incomplete=audit_incomplete,
            sidecar_gaps=gaps[0],
            error=f"communicate_failed: {exc}",
            reason_code="communicate_failed",
            dirty_admission=spec.dirty_admission,
        )

    # streaming-json: split per line, snap first sessionId, sidecar-cap each chunk.
    resolved_session_id: str | None = None
    for line in stdout_b.splitlines():
        if resolved_session_id is None:
            resolved_session_id = _snap_session_id(line)
        _try_append_sidecar_chunk(
            sidecar,
            phase="stdout_chunk",
            data=line.decode(errors="replace"),
            cap=SIDECAR_STDOUT_LINE_MAX,
            gaps=gaps,
        )

    if stderr_b:
        _try_append_sidecar_chunk(
            sidecar,
            phase="stderr_chunk",
            data=stderr_b.decode(errors="replace"),
            cap=SIDECAR_STDERR_BYTE_MAX,
            gaps=gaps,
        )

    git_status_post, git_diff_stat, audit_incomplete = await _capture_post_state(
        spec.cwd
    )
    exit_code = proc.returncode
    duration_s = time.monotonic() - t0
    status: Literal["completed", "failed"] = "completed" if exit_code == 0 else "failed"
    reason_code = "" if status == "completed" else "grok_nonzero_exit"

    # Compute truncation BEFORE the exit-record write so it can be persisted
    # to the sidecar (review C2). Without persistence, the fetch_result decode
    # path has to recompute from reconstructed (possibly truncated) chunks,
    # which can flip True→False across decode.
    stdout, truncated = _truncate_tail(stdout_b, STDOUT_MAX)
    stderr, _ = _truncate_tail(stderr_b, STDERR_MAX)

    _try_append_sidecar(
        sidecar,
        {
            "phase": "exit",
            "ts": int(time.time() * 1000),
            "status": status,
            "exit_code": exit_code,
            "duration_s": duration_s,
            "git_status_post": git_status_post,
            "git_diff_stat": git_diff_stat,
            "audit_incomplete": audit_incomplete,
            "sidecar_gaps": gaps[0],
            "resolved_session_id": resolved_session_id,
            "reason_code": reason_code,
            "truncated": truncated,
        },
        gaps,
    )

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
        reason_code=reason_code,
        resolved_session_id=resolved_session_id,
        dirty_admission=spec.dirty_admission,
    )
