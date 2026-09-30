"""Begin-drain retry for a git-worker whose HTTP surface stalls while its pid lives.

An HTTP timeout on ``POST .../begin-drain`` is not evidence that the worker is
gone. GIW's event loop can stall past the 10s control-plane budget while the
process is alive and still serving cursor-sdk bridges; one such timeout reaped
six live conductors when the supervisor took it for a dead target (a:36911).
So the supervisor retries begin-drain every reconcile window for as long as the
health/pid probe still sees a process, alerts once after the unreachable
window, and hands the failure to the kill-without-epoch path only when

  * the probe reports the process absent (SIGTERM / start is then correct), or
  * recycle mode has exhausted ``idle_escalate_s`` — a pid-alive GIW whose
    HTTP is wedged for good must still recycle to a kill (a:36019).

Cancel / force observed mid-retry returns ``None`` so the caller runs its
ordinary abort path. The probe contract is the same one the await loop uses:
an unknown probe is not "absent".
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from universal_logging import get_logger

from . import git_worker_liveness as _liveness
from .restart_intent_store import Intent

logger = get_logger(__name__)


class BeginDrainSeat(Protocol):
    """The supervisor surface this retry needs."""

    reconcile_interval_s: float
    idle_escalate_s: float | None

    async def _begin_drain(self, intent: Intent) -> Intent: ...

    def _abort_kind(self, intent: Intent) -> str | None: ...

    async def _health_pid_absent(self) -> bool: ...

    async def _emit_probe_unreachable(
        self, intent: Intent, *, elapsed_s: float, consecutive_failures: int
    ) -> None: ...


async def begin_drain_or_wait(
    seat: BeginDrainSeat, intent: Intent, *, t0: float
) -> Intent | None:
    """Drive begin-drain until it lands; ``None`` when cancel/force was observed.

    Re-raises the begin-drain error only when the kill path is warranted: the
    health/pid probe reports no process, or recycle mode has waited
    ``idle_escalate_s`` on a pid-alive worker that never answered.
    """
    threshold = _liveness.polls_for_window(
        _liveness.unreachable_window_s(), seat.reconcile_interval_s
    )
    failures = 0
    alerted = False
    while True:
        try:
            return await seat._begin_drain(intent)
        except Exception:
            if seat._abort_kind(intent) is not None:
                return None
            if await seat._health_pid_absent():
                raise
            failures += 1
            elapsed = time.monotonic() - t0
            if seat.idle_escalate_s is not None and elapsed >= seat.idle_escalate_s:
                raise
            if failures == 1:
                logger.warning(
                    "begin-drain unreachable but pid alive; retrying, not killing: "
                    "intent_id=%s",
                    intent.intent_id,
                )
            if not alerted and failures >= threshold:
                await seat._emit_probe_unreachable(
                    intent, elapsed_s=elapsed, consecutive_failures=failures
                )
                alerted = True
        await asyncio.sleep(seat.reconcile_interval_s)


__all__ = ["BeginDrainSeat", "begin_drain_or_wait"]
