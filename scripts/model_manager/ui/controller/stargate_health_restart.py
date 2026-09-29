"""Restart stargate when /health stays dead while the process is up.

``check_stargate`` marks a live PID unhealthy when ``/health`` misses the 2s
probe. This loop is the act: after ``N`` consecutive dead probes past a boot
grace, force a restart. ``13102``'s idle-drain supervisor is not in tree, so
the restart uses ``force=True`` (drain skipped).

A timeout after TCP accept is not a dead probe. The port is open and the
handler did not finish inside the client budget; the same endpoint often
answers a slightly longer probe. Counting those misses force-restarts a
process that is still serving.

Falsifier: status ``running`` or ``stopped`` resets the streak and does not
restart. Unhealthy during the boot grace does not increment. A timeout-class
detail holds the streak and does not restart. Restart fires only when the
post-grace streak of non-timeout unhealthy probes reaches ``N``.
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

# Exception class names ``check_stargate`` / ``observe_cdp_ask_health`` put in
# the probe detail. httpx timeouts are not ``TimeoutError``.
_SLOW_ANSWER_MARKERS = (
    "TimeoutError",
    "TimeoutException",
    "ReadTimeout",
    "ConnectTimeout",
    "WriteTimeout",
    "PoolTimeout",
)


def detail_is_slow_answer(detail: str | None) -> bool:
    """True when the probe note names an HTTP timeout, not a dead listener.

    ``check_stargate`` calls ``/health`` only after TCP accept succeeds. A
    timeout there means the process accepted and did not finish the response
    inside the client budget. ``ConnectError`` and ``port not responding``
    do not match.
    """
    if not detail:
        return False
    return any(marker in detail for marker in _SLOW_ANSWER_MARKERS)


def note_stargate_probe(
    streak: int,
    status: str,
    *,
    uptime_s: float | None,
    n: int = STARGATE_HEALTH_RESTART_N,
    boot_grace_s: float = STARGATE_BOOT_GRACE_S,
    detail: str | None = None,
) -> tuple[int, bool]:
    """Return ``(new_streak, should_restart)`` for one probe.

    ``unhealthy`` before ``boot_grace_s`` holds the streak (startup has not
    had time to bind). A slow answer holds it too: the miss is not evidence
    the process is dead. Any other non-unhealthy status clears it.
    """
    if status != "unhealthy":
        return 0, False
    if uptime_s is None or uptime_s < boot_grace_s:
        return streak, False
    if detail_is_slow_answer(detail):
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
            self._streak,
            info.status.value,
            uptime_s=uptime_s,
            detail=info.detail,
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
    """Stop then start stargate.

    The new process loads the pipeline registry. That load reuses the on-disk
    snapshot when sources and availability decisions are unchanged, so a flap
    does not full-build the registry once per restart.
    """
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
