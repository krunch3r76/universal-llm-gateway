"""Bridge-close convergence after a park sweep (spec D4.3).

``_maybe_emit_drain_completed`` fires only at ticket close and would miss a
bridge process that lingers a second past ``client.close()`` — the drain
would then wait on the manage reconcile poll instead of the event. This
coroutine waits for the live-bridge count to reach zero under an *idle*
budget (progress = count strictly decreasing; never a wall clock,
``[universal:obs-over-timeouts]``), then asks the admission controller to
re-check idle so ``git_worker.drain.completed`` is emitted. On stall, only
bridges still registered for parked dispatch ids are aborted — their runs are
already cancelled and their stores consistent (Leg 0a).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_orphan import (
    abort_orphaned_bridge,
    active_bridge_dispatch_ids,
)
from services.git_integration_worker.cursor_sdk_park_events import (
    emit_sdk_park_bridge_abort_escalated,
)

logger = get_logger(__name__)

_DEFAULT_BRIDGE_CLOSE_IDLE_S = 30.0
_BRIDGE_CLOSE_POLL_S = 1.0


def bridge_close_idle_budget_s() -> float:
    raw = os.environ.get("CURSOR_SDK_PARK_BRIDGE_CLOSE_IDLE_S", "").strip()
    if not raw:
        return _DEFAULT_BRIDGE_CLOSE_IDLE_S
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_BRIDGE_CLOSE_IDLE_S


async def converge_bridges_after_park(
    *,
    parked_ids: list[str],
    intent_id: str | None,
    controller: Any,
    count_bridges: Any | None = None,
    idle_budget_s: float | None = None,
    poll_s: float = _BRIDGE_CLOSE_POLL_S,
) -> int:
    """Wait for parked bridges to exit, then ``controller.recheck_drain_idle()``.

    Returns the number of lingering parked bridges that had to be aborted.
    """
    from services.git_integration_worker.cursor_sdk_restart_bridge_gate import (
        count_live_operator_bridges,
    )

    counter = count_bridges or count_live_operator_bridges
    budget = (
        idle_budget_s if idle_budget_s is not None else bridge_close_idle_budget_s()
    )
    last_count = await asyncio.to_thread(counter)
    last_progress = time.monotonic()
    aborted = 0
    while last_count > 0:
        await asyncio.sleep(poll_s)
        count = await asyncio.to_thread(counter)
        now = time.monotonic()
        if count < last_count:
            last_progress = now
        last_count = count
        if count == 0:
            break
        if now - last_progress < budget:
            continue
        lingering = [d for d in parked_ids if d in active_bridge_dispatch_ids()]
        if not lingering:
            logger.info(
                "park bridge convergence stalled on non-parked bridges intent_id=%s "
                "live=%s — leaving them to the drain gate",
                intent_id,
                count,
            )
            break
        for dispatch_id in lingering:
            if await asyncio.to_thread(abort_orphaned_bridge, dispatch_id=dispatch_id):
                aborted += 1
                emit_sdk_park_bridge_abort_escalated(
                    dispatch_id=dispatch_id, intent_id=intent_id
                )
        last_progress = now
        last_count = await asyncio.to_thread(counter)
    controller.recheck_drain_idle()
    return aborted


__all__ = ["bridge_close_idle_budget_s", "converge_bridges_after_park"]
