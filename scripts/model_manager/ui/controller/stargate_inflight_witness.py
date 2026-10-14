"""Stargate down witness — port/PID checks when the active-work probe refuses connection."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .restart_drain_witness import WitnessReport
from .service_config import GATEWAY_DIR

_SOURCE = "stargate_down_witness"

_port_open_fn: Callable[[int], bool] | None = None
_pid_alive_fn: Callable[[int], bool] | None = None
_read_pid_fn: Callable[[], int | None] | None = None


def _stargate_port() -> int:
    return int(os.environ.get("STARGATE_PORT", "9999"))


def _default_port_open(port: int) -> bool:
    from ..model.service_state import ServiceState

    return ServiceState._port_open(port)


def _default_pid_alive(pid: int) -> bool:
    from ..model.service_state import ServiceState

    return ServiceState._pid_alive(pid)


def _default_read_stargate_pid() -> int | None:
    pid_file = GATEWAY_DIR / "stargate.pid"
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def stargate_down_witness_agrees() -> bool:
    """True when the Stargate port is closed and the PID file is absent or dead."""
    port = _stargate_port()
    port_open = (_port_open_fn or _default_port_open)(port)
    if port_open:
        return False
    pid = (_read_pid_fn or _default_read_stargate_pid)()
    if pid is None:
        return True
    return not (_pid_alive_fn or _default_pid_alive)(pid)


def stargate_connect_refused(exc: BaseException) -> bool:
    """True when *exc* is an httpx connect failure with errno ECONNREFUSED."""
    if not isinstance(exc, httpx.ConnectError):
        return False
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, OSError) and cause.errno == errno.ECONNREFUSED:
            return True
        cause = cause.__cause__ if isinstance(cause.__cause__, BaseException) else None
    return False


class StargateDownWitness:
    """``InFlightWitness`` mirroring ``stop_stargate`` port/PID down detection."""

    async def observe(self, service: str) -> WitnessReport:
        if service != "stargate":
            return WitnessReport(
                verdict="unknown",
                source=_SOURCE,
                note=f"not applicable to {service}",
            )
        if stargate_down_witness_agrees():
            return WitnessReport(
                verdict="idle",
                source=_SOURCE,
                detail={"probe": "econnrefused", "busy": False},
            )
        return WitnessReport(
            verdict="busy",
            source=_SOURCE,
            note="stargate port open or pid alive despite connect refused",
            detail={"busy": True},
        )


__all__ = [
    "StargateDownWitness",
    "stargate_connect_refused",
    "stargate_down_witness_agrees",
]
