"""Restart stargate when /health stays dead while the process is up.

``check_stargate`` already marks a live PID unhealthy when ``/health`` misses
the 2s probe. This loop is the missing act: after ``N`` consecutive unhealthy
probes past a boot grace, force a restart. ``13102``'s idle-drain supervisor
is not in tree, so the restart uses ``force=True`` (drain skipped).

Falsifier: status ``running`` or ``stopped`` resets the streak and does not
restart. Unhealthy during the boot grace does not increment. Restart fires
only when the post-grace unhealthy streak reaches ``N``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service_ctl.core import ServiceController

logger = logging.getLogger(__name__)

STARGATE_HEALTH_RESTART_N = 3
STARGATE_HEALTH_PROBE_S = 5.0
STARGATE_BOOT_GRACE_S = 60.0


def note_stargate_probe(
    streak: int,
    status: str,
    *,
    uptime_s: float | None,
    n: int = STARGATE_HEALTH_RESTART_N,
    boot_grace_s: float = STARGATE_BOOT_GRACE_S,
) -> tuple[int, bool]:
    """Return ``(new_streak, should_restart)`` for one probe.

    ``unhealthy`` before ``boot_grace_s`` holds the streak (startup has not
    had time to bind). Any other status clears it.
    """
    if status != "unhealthy":
        return 0, False
    if uptime_s is None or uptime_s < boot_grace_s:
        return streak, False
    streak += 1
    return streak, streak >= n


def process_uptime_s(pid: int) -> float | None:
    """Seconds since ``pid`` started, from ``/proc`` clock ticks."""
    try:
        clk = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime", encoding="ascii") as uptime_file:
            system_up = float(uptime_file.read().split()[0])
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat_file:
            fields = stat_file.read().split()
        start_ticks = int(fields[21])
    except (OSError, ValueError, IndexError):
        return None
    return system_up - (start_ticks / clk)


class StargateHealthRestart:
    """Background probe. Force-restarts stargate after N post-grace misses."""

    def __init__(self, controller: ServiceController) -> None:
        self._controller = controller
        self._streak = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="stargate-health-restart")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(STARGATE_HEALTH_PROBE_S)
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("stargate health restart tick failed")

    async def _tick(self) -> None:
        info = await asyncio.to_thread(self._controller.service_state.check_stargate)
        uptime_s = process_uptime_s(info.pid) if info.pid is not None else None
        self._streak, restart = note_stargate_probe(
            self._streak, info.status.value, uptime_s=uptime_s
        )
        if not restart:
            return
        self._streak = 0
        logger.warning(
            "stargate /health missed %d consecutive probes past boot grace; "
            "force restart",
            STARGATE_HEALTH_RESTART_N,
        )
        result = await _force_restart_stargate(self._controller)
        logger.warning("stargate health restart result: %s", result.get("status"))


async def _force_restart_stargate(controller: ServiceController) -> dict[str, object]:
    """Same chokepoint as manage restart, with force because drain-13102 is absent."""
    from ..api_dispatch import _lifecycle_with_restart_window, _restart_cycle
    from .restart_drain import run_gated

    return await run_gated(
        controller.restart_gate,
        "restart",
        "stargate",
        force=True,
        lifecycle=lambda: _lifecycle_with_restart_window(
            controller,
            "stargate",
            "restart",
            lambda: _restart_cycle(controller, "stargate"),
        ),
    )
