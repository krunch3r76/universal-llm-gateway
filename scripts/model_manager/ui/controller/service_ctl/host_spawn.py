"""Detached host-process spawn — survives manage TUI event-loop shutdown.

``asyncio.create_subprocess_exec`` registers a ``BaseSubprocessTransport``. When
the Textual event loop closes on ``q`` / quit, ``BaseSubprocessTransport.close``
calls ``proc.kill()`` on every still-running child — even when the child was
started with ``start_new_session=True``. That is the mechanism behind
``todo:manage-quit-must-not-stop-fleet`` (live repro 2026-07-28).

Long-lived host services must therefore be spawned with ``subprocess.Popen`` so
asyncio never owns a transport. Session detachment (``start_new_session=True``)
remains for terminal/SIGHUP hygiene.

When a user systemd manager is available, services may be wrapped in a transient
scope (``systemd-run --user --scope``) so each process gets its own cgroup
instead of inheriting the manage tmux pane scope.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .startup_probe import (
    DEFAULT_STARTUP_CEILING_S,
    DEFAULT_STARTUP_INTERVAL_S,
    StartupOutcome,
)

logger = logging.getLogger(__name__)

_SYSTEMD_RUN = "systemd-run"
_SYSTEMCTL = "systemctl"
_SCOPE_UNIT_PREFIX = "ulg-"

_systemd_scope_wrapping_available: bool | None = None
_systemd_unavailable_reason_cached: str | None = None


def sanitise_scope_name(name: str) -> str:
    """Map a service identity string to a valid systemd unit name fragment."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-_.")
    if not cleaned:
        msg = f"scope_name {name!r} sanitizes to empty systemd unit fragment"
        raise ValueError(msg)
    return cleaned


def _probe_user_systemd_manager() -> tuple[bool, str]:
    if shutil.which(_SYSTEMD_RUN) is None:
        return False, "systemd-run not found in PATH"
    if shutil.which(_SYSTEMCTL) is None:
        return False, "systemctl not found in PATH"
    try:
        result = subprocess.run(
            [_SYSTEMCTL, "--user", "show", "--property=ActiveState", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"user systemd manager probe failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, (
            f"user systemd manager not reachable ({detail or 'systemctl exit nonzero'})"
        )
    state = result.stdout.strip()
    if not state:
        return False, "user systemd manager returned empty ActiveState"
    return True, f"user systemd manager ActiveState={state!r}"


def is_systemd_scope_wrapping_available() -> bool:
    """Return whether transient user scopes can wrap host spawns."""
    global _systemd_scope_wrapping_available, _systemd_unavailable_reason_cached
    if _systemd_scope_wrapping_available is not None:
        return _systemd_scope_wrapping_available
    available, reason = _probe_user_systemd_manager()
    _systemd_scope_wrapping_available = available
    if not available:
        _systemd_unavailable_reason_cached = reason
    return available


def _systemd_unavailable_reason() -> str:
    if _systemd_unavailable_reason_cached is not None:
        return _systemd_unavailable_reason_cached
    available, reason = _probe_user_systemd_manager()
    return reason if not available else "systemd scope wrapping disabled"


def scope_unit_name(scope_name: str) -> str:
    """Full transient unit name (with ``.scope``) for *scope_name*."""
    return f"{_SCOPE_UNIT_PREFIX}{sanitise_scope_name(scope_name)}.scope"


def scope_unit_is_active(unit: str) -> bool:
    """Whether *unit* currently holds live processes."""
    try:
        result = subprocess.run(
            [_SYSTEMCTL, "--user", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == "active"


def clear_stale_scope_unit(unit: str) -> None:
    """Release a leftover unit name so a restart can reuse it.

    ``--collect`` only reaps a scope once its own processes exit, so a unit
    left in ``failed`` state by an abnormal exit keeps owning the name. Naming
    an existing unit makes ``systemd-run`` exit non-zero, which would surface
    as the service failing to start rather than as a wrapping problem.
    """
    try:
        subprocess.run(
            [_SYSTEMCTL, "--user", "reset-failed", unit],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not reset stale scope %s: %s", unit, exc)


def build_scope_wrapped_argv(
    args: Sequence[str],
    *,
    scope_name: str,
    memory_max: str | None = None,
) -> list[str]:
    """Prefix *args* with ``systemd-run --user --scope`` when wrapping applies."""
    unit = f"{_SCOPE_UNIT_PREFIX}{sanitise_scope_name(scope_name)}"
    wrapped: list[str] = [
        _SYSTEMD_RUN,
        "--user",
        "--scope",
        "--collect",
        f"--unit={unit}",
    ]
    if memory_max is not None:
        wrapped.extend(["-p", f"MemoryMax={memory_max}"])
    wrapped.append("--")
    wrapped.extend(args)
    return wrapped


def spawn_detached_host_process(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str],
    log_file: Path,
    scope_name: str | None = None,
    memory_max: str | None = None,
) -> subprocess.Popen[bytes]:
    """Spawn a long-lived host service that outlives the manage TUI process.

    Opens ``log_file`` for the child's stdout/stderr, then closes the parent
    handle after ``Popen`` returns (the child keeps its inherited FD).

    When ``scope_name`` is set and user systemd scope wrapping is available,
    the argv is prefixed with ``systemd-run --user --scope --collect`` so the
    child lands in its own transient cgroup. On unavailable wrapping, the
    original argv is used and a warning is logged — spawn never fails solely
    because scope wrapping is unavailable.
    """
    spawn_args: list[str] = list(args)
    if scope_name is not None:
        if is_systemd_scope_wrapping_available():
            unit = scope_unit_name(scope_name)
            if scope_unit_is_active(unit):
                # Naming a live unit makes systemd-run exit non-zero, which
                # would read as "the service failed to start". Spawning
                # unwrapped is exactly the pre-scope behaviour, so a lingering
                # predecessor costs isolation for this start, never the start.
                logger.warning(
                    "scope %s still active; spawning %s without scope",
                    unit,
                    scope_name,
                )
            else:
                clear_stale_scope_unit(unit)
                spawn_args = build_scope_wrapped_argv(
                    args,
                    scope_name=scope_name,
                    memory_max=memory_max,
                )
        else:
            logger.warning(
                "systemd scope wrapping unavailable for %s (%s); "
                "spawning without scope",
                scope_name,
                _systemd_unavailable_reason(),
            )
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_file.open("wb")
    try:
        return subprocess.Popen(
            spawn_args,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            env=dict(env),
            start_new_session=True,
        )
    finally:
        log_fh.close()


async def await_popen_started(
    process: subprocess.Popen[bytes],
    *,
    ready: Callable[[], bool] | None = None,
    ceiling_s: float = DEFAULT_STARTUP_CEILING_S,
    interval_s: float = DEFAULT_STARTUP_INTERVAL_S,
) -> tuple[StartupOutcome, int | None]:
    """Poll a ``Popen`` child until ready, crashed, or ``ceiling_s`` elapsed."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ceiling_s
    while loop.time() < deadline:
        code = process.poll()
        if code is not None:
            return StartupOutcome.CRASHED, code
        if ready is not None and await loop.run_in_executor(None, ready):
            return StartupOutcome.READY, None
        await asyncio.sleep(interval_s)
    code = process.poll()
    if code is not None:
        return StartupOutcome.CRASHED, code
    return StartupOutcome.ALIVE, None
