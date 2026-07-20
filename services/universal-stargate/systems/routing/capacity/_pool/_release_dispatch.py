"""
CapacityPool mixin: token release and FIFO waiter dispatch loop.

On release, decrements the slot and wakes the next queued waiter if present.
"""

from __future__ import annotations

import asyncio
from collections import deque

from universal_logging import get_logger

from ._types import CapacityToken, _Slot, _Waiter

logger = get_logger(__name__)


class _ReleaseDispatchMixin:
    async def _release(self, token: CapacityToken) -> None:
        """Return a capacity slot and dispatch waiting requests.

        Always invokes _dispatch even when in_flight is already 0 (e.g. after
        remove_model/remove_gateway race): the queue may have waiters that
        can be served on other gateways. Cleans up deferred zero-capacity
        slots when in_flight drains to zero.
        """
        slot = _Slot(gateway_id=token.gateway_id, model_id=token.model_id)
        in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]

        if in_flight <= 0:
            logger.error(
                "Invariant: releasing %s but in_flight=%d on %s/%s "
                "(proceeding to dispatch anyway)",
                token.request_id,
                in_flight,
                token.gateway_id,
                token.model_id,
            )
        else:
            self._in_flight[slot] = in_flight - 1  # type: ignore[attr-defined]
            held = token.held_ms
            log = logger.info if held > 10_000 else logger.debug
            log(
                "Released: %s on %s/%s (held %.0fms, in_flight: %d → %d)",
                token.request_id,
                token.gateway_id,
                token.model_id,
                held,
                in_flight,
                in_flight - 1,
            )

        current = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
        if current == 0 and self._capacity.get(slot, -1) == 0:  # type: ignore[attr-defined]
            del self._capacity[slot]  # type: ignore[attr-defined]
            del self._in_flight[slot]  # type: ignore[attr-defined]
            logger.info(
                "Deferred slot cleanup: %s/%s drained",
                token.gateway_id,
                token.model_id,
            )

        await self._dispatch(token.model_id)  # type: ignore[attr-defined]

    async def _dispatch(self, model_id: str) -> None:
        """Evaluate the full FIFO queue and assign capacity to serviceable waiters.

        Skips waiters whose allowed_gateway_ids have no available slots instead
        of stopping at the first unservable one (head-of-line blocking fix).
        Unservable waiters are retained in queue order for future dispatch calls.

        Handles cancelled waiters: Task.cancel() cancels the underlying future,
        so we skip them to avoid incrementing in_flight for unclaimed slots.
        Guards against the narrow race where cancellation arrives between
        _try_immediate (which increments in_flight) and set_result.

        When model_id is under an active admission pause, _try_immediate returns
        None and every waiter is retained in queue order — no admissions fire
        until the pause expires or is explicitly released.
        """
        queue = self._queues.get(model_id)  # type: ignore[attr-defined]
        if not queue:
            return

        unservable: deque[_Waiter] = deque()
        dispatched = 0
        skipped = 0

        while queue:
            waiter = queue.popleft()

            if waiter.future.done():
                continue

            gw_id = self._try_immediate(  # type: ignore[attr-defined]
                waiter.request_id,
                model_id,
                waiter.allowed_gateway_ids,
            )
            if gw_id is None:
                unservable.append(waiter)
                skipped += 1
                continue

            if waiter.future.done():
                slot = _Slot(gateway_id=gw_id, model_id=model_id)
                self._in_flight[slot] = max(0, self._in_flight.get(slot, 0) - 1)  # type: ignore[attr-defined]
                logger.warning(
                    "Cancelled between admit and set_result: "
                    "%s on %s/%s — released slot",
                    waiter.request_id,
                    gw_id,
                    model_id,
                )
                continue

            try:
                waiter.future.set_result(gw_id)
                dispatched += 1
            except asyncio.InvalidStateError:
                # Cancellation can race between done() check and set_result().
                # In that window _try_immediate already incremented in_flight,
                # so we must release the slot to avoid a permanent leak.
                slot = _Slot(gateway_id=gw_id, model_id=model_id)
                self._in_flight[slot] = max(0, self._in_flight.get(slot, 0) - 1)  # type: ignore[attr-defined]
                logger.warning(
                    "Cancelled during set_result race: %s on %s/%s — released slot",
                    waiter.request_id,
                    gw_id,
                    model_id,
                )

        if dispatched > 0:
            logger.info(
                "Dispatched %d waiter(s) for %s (%d skipped, %d remain)",
                dispatched,
                model_id,
                skipped,
                len(unservable),
            )

        if unservable:
            self._queues[model_id] = unservable  # type: ignore[attr-defined]
        else:
            self._queues.pop(model_id, None)  # type: ignore[attr-defined]
