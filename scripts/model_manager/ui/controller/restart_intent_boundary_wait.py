"""Wait-for-row-boundary arm phase (a:37197).

When ``wait_for_boundary`` is set, the supervisor polls GIW ``drain_state``
until ``active_count == 0`` (idle / row boundary) **before** ``begin_drain``.
Admits stay open until the drain epoch flips. Optional ``expires_at`` still
cancels via the normal expiry path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from universal_logging import get_logger

from .restart_intent_expiry import expire_via_cancel
from .restart_intent_store import Intent

logger = get_logger(__name__)

__all__ = ["BoundaryWaitSeat", "wait_for_idle_boundary"]


class BoundaryWaitSeat(Protocol):
    """Supervisor surface the pre-drain wait needs."""

    reconcile_interval_s: float
    progress_interval_s: float
    store: Any

    async def drain_state(self) -> dict[str, Any]: ...

    def _abort_kind(self, intent: Intent) -> str | None: ...

    async def _emit_progress(self, intent: Intent, elapsed_s: float) -> None: ...

    cancel_drain: Any


async def wait_for_idle_boundary(
    seat: BoundaryWaitSeat, intent: Intent, *, t0: float
) -> bool:
    """Block until GIW reports idle, or abort/expire.

    Returns True when ``active_count == 0`` and begin-drain may proceed.
    Returns False when cancel/force/expiry won — caller must not begin_drain.
    """
    last_progress = t0
    while True:
        if seat._abort_kind(intent) is not None:
            return False
        now = time.monotonic()
        if now - last_progress >= seat.progress_interval_s:
            await seat._emit_progress(intent, now - t0)
            try:
                expired = await expire_via_cancel(
                    seat.store,
                    intent.intent_id,
                    release_drain=seat.cancel_drain,
                )
            except Exception:
                logger.warning(
                    "wait_for_boundary expiry tick failed; staying armed "
                    "intent_id=%s",
                    intent.intent_id,
                    exc_info=True,
                )
                expired = False
            if expired:
                return False
            last_progress = now
        try:
            snapshot = await seat.drain_state()
        except Exception:
            logger.debug(
                "wait_for_boundary drain_state probe failed intent_id=%s",
                intent.intent_id,
                exc_info=True,
            )
            snapshot = None
        if isinstance(snapshot, dict):
            active = snapshot.get("active_count")
            if isinstance(active, int) and active <= 0:
                logger.info(
                    "wait_for_boundary idle reached; begin_drain may proceed "
                    "intent_id=%s",
                    intent.intent_id,
                )
                return True
        await asyncio.sleep(seat.reconcile_interval_s)
