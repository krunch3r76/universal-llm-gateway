"""Subprocess startup probing — readiness vs crash detection.

Replaces fixed-duration ``wait_for(process.wait(), timeout=3)`` survival sleeps
with a poll loop: fail fast on crash, succeed as soon as ``ready()`` is true,
otherwise treat surviving ``ceiling_s`` as started (legacy fallback).
"""

from __future__ import annotations

import asyncio
import socket as socket_mod
from collections.abc import Callable
from enum import Enum
from pathlib import Path

DEFAULT_STARTUP_CEILING_S = 3.0
DEFAULT_STARTUP_INTERVAL_S = 0.1


class StartupOutcome(Enum):
    READY = "ready"
    ALIVE = "alive"
    CRASHED = "crashed"


def uds_socket_live(socket_path: Path) -> bool:
    """Return True when a process is accepting connections on a UDS path."""
    if not socket_path.exists():
        return False
    try:
        with socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            probe.connect(str(socket_path))
        return True
    except (ConnectionRefusedError, OSError):
        return False


async def await_subprocess_started(
    process: asyncio.subprocess.Process,
    *,
    ready: Callable[[], bool] | None = None,
    ceiling_s: float = DEFAULT_STARTUP_CEILING_S,
    interval_s: float = DEFAULT_STARTUP_INTERVAL_S,
) -> tuple[StartupOutcome, int | None]:
    """Poll until the child is ready, crashed, or has survived ``ceiling_s``.

    Returns:
        (StartupOutcome, exit_code) where exit_code is set only for CRASHED.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ceiling_s
    while loop.time() < deadline:
        if process.returncode is not None:
            return StartupOutcome.CRASHED, process.returncode
        if ready is not None and await loop.run_in_executor(None, ready):
            return StartupOutcome.READY, None
        await asyncio.sleep(interval_s)
    if process.returncode is not None:
        return StartupOutcome.CRASHED, process.returncode
    return StartupOutcome.ALIVE, None
