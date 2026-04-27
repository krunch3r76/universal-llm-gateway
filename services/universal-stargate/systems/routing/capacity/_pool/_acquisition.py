"""Mixin: capacity acquisition — acquire_token, acquire context manager, immediate try, wait loop."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from universal_logging import get_logger

from ._types import (
    CapacityToken,
    QueueFullError,
    _Slot,
    _WAITING_EVENT_INTERVAL_S,
    _Waiter,
    _WaiterCancelledError,
)

logger = get_logger(__name__)


class _AcquisitionMixin:
    async def acquire_token(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
        timeout_s: float | None = None,
    ) -> CapacityToken:
        """
        Acquire a capacity slot, returning a self-releasing token.

        Caller MUST call token.release() when done (typically in a finally block).
        For scoped usage, prefer the acquire() context manager instead.

        Raises:
            asyncio.CancelledError: if request cancelled
        """
        del timeout_s
        self._ensure_subscribed()  # type: ignore[attr-defined]
        gateway_id = self._try_immediate(request_id, model_id, allowed_gateway_ids)
        queued = False

        if gateway_id is None:
            current_depth = len(self._queues.get(model_id, deque()))  # type: ignore[attr-defined]
            if self._max_queue_depth > 0 and current_depth >= self._max_queue_depth:  # type: ignore[attr-defined]
                logger.warning(
                    "Queue full for %s: depth=%d, max=%d — rejecting %s",
                    model_id,
                    current_depth,
                    self._max_queue_depth,  # type: ignore[attr-defined]
                    request_id,
                )
                self._emit_queue_full(request_id, model_id, current_depth)  # type: ignore[attr-defined]
                raise QueueFullError(model_id, current_depth, self._max_queue_depth)  # type: ignore[attr-defined]

            queued = True
            queue_position = current_depth + 1
            logger.info(
                "🔍 acquire_token: no immediate slot for %s/%s "
                "(allowed_gws=%s, position=%d) — queueing",
                request_id,
                model_id,
                list(allowed_gateway_ids),
                queue_position,
            )
            self._emit_queue_entered(  # type: ignore[attr-defined]
                request_id,
                model_id,
                queue_position,
                len(allowed_gateway_ids),
            )
            queue_start = time.monotonic()
            gateway_id = await self._wait_for_slot(
                request_id,
                model_id,
                allowed_gateway_ids,
            )
            wait_ms = (time.monotonic() - queue_start) * 1000
            self._emit_queue_admitted(request_id, model_id, gateway_id, wait_ms)  # type: ignore[attr-defined]

        return CapacityToken(
            gateway_id=gateway_id,
            model_id=model_id,
            request_id=request_id,
            queued=queued,
            _pool=self,  # type: ignore[arg-type]
        )

    @asynccontextmanager
    async def acquire(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
        timeout_s: float | None = None,
    ) -> AsyncIterator[CapacityToken]:
        """
        Acquire a capacity slot, yielding a self-releasing token.

        Token is auto-released on context exit (normal or exception).
        """
        token = await self.acquire_token(
            request_id,
            model_id,
            allowed_gateway_ids,
            timeout_s,
        )
        try:
            yield token
        finally:
            await token.release()

    def _try_immediate(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> str | None:
        """Try to reserve a slot immediately without queuing.

        Returns gateway_id on success, None if no capacity.  Ranks
        allowed gateways by available capacity (descending, random tiebreak)
        and increments in_flight on the best.  Called both from acquire_token
        (initial attempt) and _dispatch (queued waiter evaluation).

        Returns None when admission for model_id is paused, even if capacity
        exists. This is the starvation-relief preemption point: the scheduler
        uses pause_admission() to force the model's in-flight count to drain
        so a competing model can evict and load.
        """
        if self.is_paused(model_id):  # type: ignore[attr-defined]
            return None

        ranked: list[tuple[str, int]] = []
        for gw_id in allowed_gateway_ids:
            available, _in_flight, _capacity = self.get_slot_info(gw_id, model_id)  # type: ignore[attr-defined]
            if available > 0:
                ranked.append((gw_id, available))

        if not ranked:
            return None

        random.shuffle(ranked)
        ranked.sort(key=lambda item: -item[1])
        gw_id = ranked[0][0]
        slot = _Slot(gateway_id=gw_id, model_id=model_id)
        self._in_flight[slot] = self._in_flight.get(slot, 0) + 1  # type: ignore[attr-defined]
        logger.debug(f"Immediate admit: {request_id} → {gw_id}/{model_id}")
        return gw_id

    async def _wait_for_slot(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> str:
        """Enqueue a waiter and block until a slot is assigned.  Returns gateway_id.

        Creates a Future, appends it to the per-model FIFO queue, and awaits
        resolution by _dispatch. Emits periodic non-terminal waiting signals
        while queued. On cancellation, handles the race
        where _dispatch may have already admitted the waiter (recovering the
        leaked slot to prevent permanent capacity loss).
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        waiter = _Waiter(
            request_id=request_id,
            allowed_gateway_ids=allowed_gateway_ids,
            future=future,
        )

        if model_id not in self._queues:  # type: ignore[attr-defined]
            self._queues[model_id] = deque()  # type: ignore[attr-defined]
        self._queues[model_id].append(waiter)  # type: ignore[attr-defined]
        pos = len(self._queues[model_id])  # type: ignore[attr-defined]
        logger.info(
            f"Queued: {request_id} for {model_id} "
            f"(position {pos}, allowed: {len(allowed_gateway_ids)} gw)"
        )

        wait_start = time.monotonic()
        try:
            while True:
                try:
                    gateway_id = await asyncio.wait_for(
                        asyncio.shield(future),
                        timeout=_WAITING_EVENT_INTERVAL_S,
                    )
                    logger.info(f"Admitted: {request_id} → {gateway_id}/{model_id}")
                    return gateway_id
                except TimeoutError:
                    wait_ms = (time.monotonic() - wait_start) * 1000
                    self._emit_queue_waiting(request_id, model_id, wait_ms)  # type: ignore[attr-defined]
                except _WaiterCancelledError as exc:
                    raise asyncio.CancelledError(exc.reason) from exc
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                try:
                    gateway_id = future.result()
                except _WaiterCancelledError:
                    raise asyncio.CancelledError("explicit_cancel") from None
                self._recover_leaked_slot(request_id, gateway_id, model_id)  # type: ignore[attr-defined]
            else:
                self._remove_waiter(  # type: ignore[attr-defined]
                    request_id,
                    reason="task_cancelled",
                    wake_with_exception=False,
                )
            raise
