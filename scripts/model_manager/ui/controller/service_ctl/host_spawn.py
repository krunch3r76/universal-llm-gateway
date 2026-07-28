"""Detached host-process spawn — survives manage TUI event-loop shutdown.

``asyncio.create_subprocess_exec`` registers a ``BaseSubprocessTransport``. When
the Textual event loop closes on ``q`` / quit, ``BaseSubprocessTransport.close``
calls ``proc.kill()`` on every still-running child — even when the child was
started with ``start_new_session=True``. That is the mechanism behind
``todo:manage-quit-must-not-stop-fleet`` (live repro 2026-07-28).

Long-lived host services must therefore be spawned with ``subprocess.Popen`` so
asyncio never owns a transport. Session detachment (``start_new_session=True``)
remains for terminal/SIGHUP hygiene.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .startup_probe import (
    DEFAULT_STARTUP_CEILING_S,
    DEFAULT_STARTUP_INTERVAL_S,
    StartupOutcome,
)


def spawn_detached_host_process(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str],
    log_file: Path,
) -> subprocess.Popen[bytes]:
    """Spawn a long-lived host service that outlives the manage TUI process.

    Opens ``log_file`` for the child's stdout/stderr, then closes the parent
    handle after ``Popen`` returns (the child keeps its inherited FD).
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_file.open("wb")
    try:
        return subprocess.Popen(
            list(args),
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
