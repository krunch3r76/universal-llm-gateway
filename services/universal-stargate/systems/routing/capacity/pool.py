"""
Event-driven capacity pool with self-releasing tokens.

Replaces CapacityLedger + AdmissionQueue + CapacityReleaseConsumer.

INVARIANT: ∀ acquire() ⟹ release() via CapacityToken.__aexit__
INVARIANT: ∀ slot: in_flight[slot] ≥ 0
INVARIANT: token release is idempotent (guard prevents double-release)
INVARIANT: ∀ dispatch-admitted waiter cancelled before token creation ⟹
           in_flight decremented and next waiter dispatched (no leaked slots)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Slot:
    """Unique identifier for a (gateway, model) capacity slot."""

    gateway_id: str
    model_id: str


@dataclass(slots=True)
class CapacityToken:
    """
    Proof of capacity reservation. Release via async context manager.

    INVARIANT: exactly one release per acquire (guarded by _released flag)
    """

    gateway_id: str
    model_id: str
    request_id: str
    queued: bool = False
    acquired_at: float = field(default_factory=time.monotonic)
    _released: bool = field(default=False, repr=False)
    _pool: CapacityPool | None = field(default=None, repr=False)

    async def release(self) -> None:
        """Idempotent release — safe to call multiple times."""
        if self._released or self._pool is None:
            return
        self._released = True
        await self._pool._release(self)

    @property
    def held_ms(self) -> float:
        return (time.monotonic() - self.acquired_at) * 1000


@dataclass(slots=True)
class _Waiter:
    """Queued request waiting for slot assignment."""

    request_id: str
    allowed_gateway_ids: frozenset[str]
    future: asyncio.Future[str]


class CapacityPool:
    """
    Per-(gateway, model) capacity pool with FIFO admission queues.

    Tokens auto-release via async context manager, eliminating the
    scattered release points that caused capacity leaks.
    """

    def __init__(self, event_bus: Any | None = None) -> None:
        self._event_bus = event_bus
        self._capacity: dict[_Slot, int] = {}
        self._in_flight: dict[_Slot, int] = {}
        self._queues: dict[str, deque[_Waiter]] = {}
        self._subscribed = False

    def _ensure_subscribed(self) -> None:
        """Subscribe to external capacity change events (lazy, once)."""
        if self._subscribed or not self._event_bus:
            return
        from src.scheduling.events import GATEWAY_RESOURCE_UPDATE

        async def on_resource_update(event: Any) -> None:
            for model_id in list(self._queues):
                await self._dispatch(model_id)

        self._event_bus.subscribe_async(GATEWAY_RESOURCE_UPDATE, on_resource_update)
        self._subscribed = True
        logger.debug("CapacityPool subscribed to gateway.resource.updated")

    # ── Capacity management (called by gateway manager on telemetry) ──

    def set_capacity(
        self,
        gateway_id: str,
        model_id: str,
        max_concurrent: int,
    ) -> None:
        """Set/update capacity from telemetry. Idempotent."""
        if max_concurrent < 0:
            logger.error(
                f"Invalid capacity {max_concurrent} for {gateway_id}/{model_id}"
            )
            max_concurrent = 0
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        old = self._capacity.get(slot, 0)
        self._capacity[slot] = max_concurrent
        self._in_flight.setdefault(slot, 0)
        if old != max_concurrent:
            logger.info(f"Capacity: {gateway_id}/{model_id}: {old} → {max_concurrent}")

    def remove_gateway(self, gateway_id: str) -> None:
        """Remove all capacity for a disconnected gateway."""
        slots = [s for s in self._capacity if s.gateway_id == gateway_id]
        for slot in slots:
            in_flight = self._in_flight.get(slot, 0)
            if in_flight > 0:
                logger.warning(
                    f"Gateway {gateway_id} removed with {in_flight} in-flight "
                    f"on {slot.model_id}"
                )
            del self._capacity[slot]
            del self._in_flight[slot]
        if slots:
            logger.info(f"Removed {len(slots)} slots for {gateway_id}")

    def remove_model(self, gateway_id: str, model_id: str) -> None:
        """Remove capacity for an unloaded model on a gateway."""
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        if slot not in self._capacity:
            return
        in_flight = self._in_flight.get(slot, 0)
        if in_flight > 0:
            logger.warning(
                f"Model {model_id} on {gateway_id} removed with {in_flight} in-flight"
            )
        del self._capacity[slot]
        del self._in_flight[slot]
        logger.info(f"Removed capacity: {gateway_id}/{model_id}")

    # ── Queries ──

    def available(self, gateway_id: str, model_id: str) -> int:
        """Available slots = capacity - in_flight. Returns 0 if unknown."""
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        return max(0, self._capacity.get(slot, 0) - self._in_flight.get(slot, 0))

    def get_slot_info(self, gateway_id: str, model_id: str) -> tuple[int, int, int]:
        """Return (available, in_flight, capacity) for diagnostics."""
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        capacity = self._capacity.get(slot, 0)
        in_flight = self._in_flight.get(slot, 0)
        return max(0, capacity - in_flight), in_flight, capacity

    def get_available_gateways(self, model_id: str) -> list[tuple[str, int]]:
        """Return [(gateway_id, available)] for available > 0, sorted desc."""
        results: list[tuple[str, int]] = []
        for slot in self._capacity:
            if slot.model_id == model_id:
                avail = self.available(slot.gateway_id, slot.model_id)
                if avail > 0:
                    results.append((slot.gateway_id, avail))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    # ── Admission ──

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
            TimeoutError: if timeout_s expires
            asyncio.CancelledError: if request cancelled
        """
        self._ensure_subscribed()
        gateway_id = self._try_immediate(request_id, model_id, allowed_gateway_ids)
        queued = False

        if gateway_id is None:
            queued = True
            gateway_id = await self._wait_for_slot(
                request_id,
                model_id,
                allowed_gateway_ids,
                timeout_s,
            )

        return CapacityToken(
            gateway_id=gateway_id,
            model_id=model_id,
            request_id=request_id,
            queued=queued,
            _pool=self,
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
        """Try to reserve a slot immediately. Returns gateway_id or None."""
        for gw_id in allowed_gateway_ids:
            slot = _Slot(gateway_id=gw_id, model_id=model_id)
            capacity = self._capacity.get(slot, 0)
            in_flight = self._in_flight.get(slot, 0)
            if in_flight < capacity:
                self._in_flight[slot] = in_flight + 1
                logger.debug(f"Immediate admit: {request_id} → {gw_id}/{model_id}")
                return gw_id
        return None

    async def _wait_for_slot(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
        timeout_s: float | None,
    ) -> str:
        """Queue and wait for a slot. Returns gateway_id."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        waiter = _Waiter(
            request_id=request_id,
            allowed_gateway_ids=allowed_gateway_ids,
            future=future,
        )

        if model_id not in self._queues:
            self._queues[model_id] = deque()
        self._queues[model_id].append(waiter)
        pos = len(self._queues[model_id])
        logger.info(
            f"Queued: {request_id} for {model_id} "
            f"(position {pos}, allowed: {len(allowed_gateway_ids)} gw)"
        )

        try:
            if timeout_s is not None:
                gateway_id = await asyncio.wait_for(future, timeout=timeout_s)
            else:
                gateway_id = await future
            logger.info(f"Admitted: {request_id} → {gateway_id}/{model_id}")
            return gateway_id
        except (TimeoutError, asyncio.CancelledError):
            # Race condition guard: _dispatch may have already admitted us
            # (popped from queue, incremented in_flight, set future result)
            # before our task was cancelled.  Without this, in_flight leaks
            # permanently — the slot is never released and all subsequent
            # requests to this model/gateway are blocked forever.
            if future.done() and not future.cancelled():
                self._recover_leaked_slot(request_id, future.result(), model_id)
            else:
                self._cancel_waiter(request_id)
            raise

    def _cancel_waiter(self, request_id: str) -> None:
        """Remove a waiter from its queue. Idempotent."""
        for model_id, queue in list(self._queues.items()):
            for i, waiter in enumerate(queue):
                if waiter.request_id == request_id:
                    del queue[i]
                    if not waiter.future.done():
                        waiter.future.cancel()
                    if not queue:
                        del self._queues[model_id]
                    return

    def _recover_leaked_slot(
        self, request_id: str, gateway_id: str, model_id: str
    ) -> None:
        """
        Release a slot that was reserved by _dispatch but never claimed as a token.

        Called when CancelledError/TimeoutError interrupts _wait_for_slot AFTER
        the future was resolved (dispatch already incremented in_flight).
        Without this recovery, in_flight is permanently stuck and the slot
        is blocked for all future requests.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        in_flight = self._in_flight.get(slot, 0)
        if in_flight <= 0:
            logger.error(
                f"Leaked slot recovery: {request_id} on {gateway_id}/{model_id} "
                f"but in_flight already 0 — possible double-recovery"
            )
            return
        self._in_flight[slot] = in_flight - 1
        logger.warning(
            f"Recovered leaked slot: {request_id} on {gateway_id}/{model_id} "
            f"(admitted by dispatch, cancelled before token creation, "
            f"in_flight: {in_flight} → {in_flight - 1})"
        )
        self._emit_slot_leak_recovered(request_id, gateway_id, model_id)
        # Wake next waiter in a new task — cannot await during cancellation
        try:
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(
                    self._dispatch(model_id),
                    name=f"capacity-recover-dispatch-{model_id}",
                )
            )
        except RuntimeError:
            pass

    def _emit_slot_leak_recovered(
        self, request_id: str, gateway_id: str, model_id: str
    ) -> None:
        """Emit event when a leaked capacity slot is recovered."""
        if not self._event_bus:
            return
        try:
            from universal_event_bus import Event

            event = Event(
                signal="capacity.slot.leak.recovered",
                payload={
                    "request_id": request_id,
                    "gateway_id": gateway_id,
                    "model_id": model_id,
                    "snapshot": self.get_snapshot(),
                },
            )
            asyncio.get_running_loop().call_soon(
                lambda: asyncio.create_task(self._event_bus.publish_async_nowait(event))
            )
        except Exception:
            pass

    # ── Release ──

    async def _release(self, token: CapacityToken) -> None:
        """Release a token's slot and dispatch waiting requests."""
        slot = _Slot(gateway_id=token.gateway_id, model_id=token.model_id)
        in_flight = self._in_flight.get(slot, 0)
        if in_flight <= 0:
            logger.error(
                f"Invariant: releasing {token.request_id} but in_flight=0 "
                f"on {token.gateway_id}/{token.model_id}"
            )
            return
        self._in_flight[slot] = in_flight - 1
        logger.debug(
            f"Released: {token.request_id} on {token.gateway_id}/{token.model_id} "
            f"(held {token.held_ms:.0f}ms)"
        )
        await self._dispatch(token.model_id)

    async def _dispatch(self, model_id: str) -> None:
        """Dispatch waiters from head of model queue.

        Handles cancelled waiters: Task.cancel() cancels the underlying future,
        so we must skip them before reserving a slot via _try_immediate.
        Also guards against the narrow race where cancellation occurs between
        _try_immediate and set_result.
        """
        queue = self._queues.get(model_id)
        if not queue:
            return

        dispatched = 0
        while queue:
            waiter = queue[0]

            # Task.cancel() propagates to the future — skip cancelled waiters
            # to avoid incrementing in_flight for a slot nobody will claim.
            if waiter.future.done():
                queue.popleft()
                continue

            gw_id = self._try_immediate(
                waiter.request_id,
                model_id,
                waiter.allowed_gateway_ids,
            )
            if gw_id is None:
                break
            queue.popleft()

            # Guard: cancellation may have arrived between _try_immediate
            # (which incremented in_flight) and here.
            if not waiter.future.done():
                waiter.future.set_result(gw_id)
                dispatched += 1
            else:
                slot = _Slot(gateway_id=gw_id, model_id=model_id)
                self._in_flight[slot] = max(0, self._in_flight.get(slot, 0) - 1)
                logger.warning(
                    f"Cancelled between admit and set_result: "
                    f"{waiter.request_id} on {gw_id}/{model_id} "
                    f"— released slot"
                )

        if dispatched > 0:
            logger.info(
                f"Dispatched {dispatched} waiter(s) for {model_id} "
                f"({len(queue)} remain)"
            )
        if not queue:
            self._queues.pop(model_id, None)

    # ── Diagnostics ──

    def get_snapshot(self) -> dict[str, Any]:
        """Return diagnostic snapshot of all capacity state."""
        return {
            "capacity": {
                f"{s.gateway_id}/{s.model_id}": c for s, c in self._capacity.items()
            },
            "in_flight": {
                f"{s.gateway_id}/{s.model_id}": c for s, c in self._in_flight.items()
            },
            "queues": {
                mid: [
                    {
                        "request_id": w.request_id,
                        "allowed_gateways": list(w.allowed_gateway_ids),
                        "done": w.future.done(),
                    }
                    for w in q
                ]
                for mid, q in self._queues.items()
            },
            "total_capacity": sum(self._capacity.values()),
            "total_in_flight": sum(self._in_flight.values()),
            "total_queued": sum(len(q) for q in self._queues.values()),
        }
