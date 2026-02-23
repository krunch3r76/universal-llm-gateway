"""
Per-model admission queue — FIFO slot assignment.

Serializes request admission for a model across all gateways.
Event-driven wake via MODEL_EXECUTION_COMPLETED/FAILED.

INVARIANT: ∀ dispatched request: ledger.try_reserve() = True
INVARIANT: FIFO ordering preserved (deque)
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from .ledger import CapacityLedger

logger = get_logger(__name__)


@dataclass(slots=True)
class _AdmissionWaiter:
    """Single queued request waiting for slot assignment."""

    request_id: str
    allowed_gateway_ids: frozenset[str]
    future: asyncio.Future[str]


class AdmissionQueue:
    """
    Per-model FIFO admission queue with event-driven dispatch.

    Queues requests when no capacity available, wakes on
    MODEL_EXECUTION_COMPLETED/FAILED and MODEL_CAPACITY_FREED events.
    """

    def __init__(
        self,
        ledger: CapacityLedger,
        event_bus: Any | None = None,
    ) -> None:
        """
        Initialize admission queue.

        Args:
            ledger: Capacity ledger for slot reservation
            event_bus: Event bus for capacity events
        """
        self._ledger = ledger
        self._event_bus = event_bus
        self._queues: dict[str, deque[_AdmissionWaiter]] = {}
        self._subscribed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _subscribe_to_events(self, loop: asyncio.AbstractEventLoop) -> None:
        """Subscribe to capacity events (lazy, loop rebinding)."""
        if self._loop is not loop:
            self._loop = loop
            logger.debug(f"AdmissionQueue loop rebound (id={id(loop)})")

        if self._subscribed or not self._event_bus:
            return

        from src.scheduling.events import (
            MODEL_CAPACITY_FREED,
            MODEL_EXECUTION_COMPLETED,
            MODEL_EXECUTION_FAILED,
        )

        async def on_capacity_event(event) -> None:
            """Dispatch waiters when capacity freed."""
            if self._loop is None or self._loop.is_closed():
                return
            model_id = event.payload.get("model_id")
            if model_id:
                self._loop.call_soon_threadsafe(self._schedule_dispatch, model_id)

        self._event_bus.subscribe_async(MODEL_EXECUTION_COMPLETED, on_capacity_event)
        self._event_bus.subscribe_async(MODEL_EXECUTION_FAILED, on_capacity_event)
        self._event_bus.subscribe_async(MODEL_CAPACITY_FREED, on_capacity_event)
        self._subscribed = True
        logger.debug(
            "AdmissionQueue subscribed to model.execution.completed, "
            "model.execution.failed, and model.capacity.freed"
        )

    def _schedule_dispatch(self, model_id: str) -> None:
        """Schedule async dispatch (called from loop context)."""
        if self._loop and not self._loop.is_closed():
            self._loop.create_task(self._dispatch(model_id))

    async def _dispatch(self, model_id: str) -> None:
        """
        Dispatch waiters from head of model queue.

        Scans FIFO from head, finds first waiter with available gateway,
        reserves slot, resolves future with gateway_id.
        """
        queue = self._queues.get(model_id)
        if not queue:
            return

        dispatched_count = 0
        while queue:
            waiter = queue[0]

            gateway_id = None
            for gw_id in waiter.allowed_gateway_ids:
                if self._ledger.available(gw_id, model_id) > 0:
                    if self._ledger.try_reserve(waiter.request_id, gw_id, model_id):
                        gateway_id = gw_id
                        break

            if gateway_id is None:
                break

            queue.popleft()
            if not waiter.future.done():
                waiter.future.set_result(gateway_id)
            dispatched_count += 1

        if dispatched_count > 0:
            logger.info(
                f"Dispatched {dispatched_count} waiter(s) for {model_id} "
                f"({len(queue)} remain)"
            )

        if not queue:
            del self._queues[model_id]

    async def acquire(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
        timeout_s: float | None = None,
    ) -> str:
        """
        Wait for a slot on any allowed gateway. Returns gateway_id assigned.

        Sticky: allowed_gateway_ids = {pinned_gateway}
        Non-sticky: allowed_gateway_ids = {all healthy gateways with model loaded}

        Args:
            request_id: Unique request identifier
            model_id: Model to acquire capacity for
            allowed_gateway_ids: Gateways that can serve this request
            timeout_s: Optional timeout in seconds

        Returns:
            gateway_id where slot was reserved

        Raises:
            TimeoutError: if timeout_s expires
            asyncio.CancelledError: if request cancelled
        """
        loop = asyncio.get_running_loop()
        self._subscribe_to_events(loop)

        for gw_id in allowed_gateway_ids:
            if self._ledger.available(gw_id, model_id) > 0:
                if self._ledger.try_reserve(request_id, gw_id, model_id):
                    logger.debug(f"Immediate admit: {request_id} → {gw_id}/{model_id}")
                    return gw_id

        future: asyncio.Future[str] = loop.create_future()
        waiter = _AdmissionWaiter(
            request_id=request_id,
            allowed_gateway_ids=allowed_gateway_ids,
            future=future,
        )

        if model_id not in self._queues:
            self._queues[model_id] = deque()
        self._queues[model_id].append(waiter)

        queue_position = len(self._queues[model_id])
        logger.info(
            f"Queued: {request_id} for {model_id} "
            f"(position {queue_position}, allowed: {len(allowed_gateway_ids)} gw)"
        )

        try:
            if timeout_s is not None:
                gateway_id = await asyncio.wait_for(future, timeout=timeout_s)
            else:
                gateway_id = await future
            logger.info(f"Admitted: {request_id} → {gateway_id}/{model_id}")
            return gateway_id
        except (TimeoutError, asyncio.CancelledError):
            self.cancel(request_id)
            raise

    def cancel(self, request_id: str) -> bool:
        """
        Remove request from queue and release any ledger reservation. Idempotent.

        Args:
            request_id: Request to cancel

        Returns:
            True if request was found and removed
        """
        for model_id, queue in list(self._queues.items()):
            for i, waiter in enumerate(queue):
                if waiter.request_id == request_id:
                    del queue[i]
                    if not waiter.future.done():
                        waiter.future.cancel()
                    # Defensive: release ledger reservation in case waiter
                    # was dispatched between _dispatch and this cancel.
                    # Idempotent — returns False if no reservation exists.
                    self._ledger.release(request_id)
                    logger.debug(f"Cancelled: {request_id} from {model_id} queue")
                    if not queue:
                        del self._queues[model_id]
                    return True
        return False

    async def release_reserved(self, request_id: str, model_id: str) -> None:
        """Release an admitted (in-flight) slot and dispatch any waiting requests.

        Used when a request fails after admission (try_reserve succeeded) but
        before model.execution.completed fires — e.g. load failure in
        federated_routing before context.selected_gateway is set.

        Without this, the in-flight count stays elevated permanently and
        waiting requests never get dispatched — queue starvation / deadlock.

        ∀ call: idempotent — ledger.release() returns False if no reservation.
        """
        released = self._ledger.release(request_id)
        if released:
            logger.debug(
                f"Released reserved slot (pre-execution): {request_id}/{model_id}"
            )
            await self._dispatch(model_id)
        else:
            logger.debug(f"release_reserved: no reservation found for {request_id}")

    def get_snapshot(self) -> dict[str, Any]:
        """Return diagnostic snapshot of queue state."""
        return {
            "queues": {
                model_id: [
                    {
                        "request_id": w.request_id,
                        "allowed_gateways": list(w.allowed_gateway_ids),
                        "done": w.future.done(),
                    }
                    for w in queue
                ]
                for model_id, queue in self._queues.items()
            },
            "total_queued": sum(len(q) for q in self._queues.values()),
        }
