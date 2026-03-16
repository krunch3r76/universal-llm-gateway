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
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from universal_logging import get_logger

logger = get_logger(__name__)


class _EventBusLike(Protocol):
    def subscribe_async(self, signal: str, callback: Any) -> None: ...

    async def publish_async_nowait(self, event: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class _Slot:
    """Unique identifier for a (gateway, model) capacity slot."""

    gateway_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.gateway_id}/{self.model_id}"


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
        """Return this reservation to the pool exactly once.

        The first call hands the slot back to `CapacityPool._release()` and
        marks the token as released. Later calls are no-ops, and tokens with no
        associated pool are also treated as no-ops, so callers can safely
        release in `finally` blocks without risking double accounting.
        """
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
    """Per-(gateway, model) capacity pool with FIFO admission queues.

    Central capacity accounting for all inference requests routed through
    Stargate.  Every execution path that forwards a request to a gateway
    MUST acquire a slot via acquire() or acquire_token().  Tokens
    auto-release via async context manager, eliminating the scattered
    release points that caused capacity leaks in the prior design.

    INVARIANT: ∀ forwarded request: exactly one acquire() → release() pair.
    Bypass of this pool is a silent correctness violation (no error, no log,
    only observable as latency degradation under load).
    """

    def __init__(self, event_bus: _EventBusLike | None = None) -> None:
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
        """Set or update max concurrent capacity for a slot.

        Called by the gateway manager when telemetry reports a capacity
        change for a (gateway, model) pair.
        Idempotent — re-setting the same value is a no-op (no log emitted).
        Negative values are clamped to 0 with an ERROR log.
        """
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
        """Remove all capacity slots for a disconnected gateway.

        Physical deletion: _capacity and _in_flight entries for the gateway.
        Deferred: slots with in_flight > 0 are zeroed in _capacity; _release
        removes them when in_flight drains to 0.
        """
        slots = [s for s in self._capacity if s.gateway_id == gateway_id]
        removed = 0
        deferred = 0
        for slot in slots:
            in_flight = self._in_flight.get(slot, 0)
            if in_flight > 0:
                self._capacity[slot] = 0
                deferred += 1
                logger.warning(
                    "Gateway %s: slot %s zeroed with %d in-flight (deferred removal)",
                    gateway_id,
                    slot.model_id,
                    in_flight,
                )
            else:
                del self._capacity[slot]
                self._in_flight.pop(slot, None)
                removed += 1
        if slots:
            logger.info(
                "Gateway %s: %d slot(s) removed, %d deferred",
                gateway_id,
                removed,
                deferred,
            )

    def remove_model(self, gateway_id: str, model_id: str) -> None:
        """Mark a model's capacity as zero after telemetry reports unload.

        Physical deletion: _capacity and _in_flight for this (gateway_id, model_id).
        Deferred: if in_flight > 0, slot is zeroed in _capacity; _release
        removes it when in_flight drains to 0.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        if slot not in self._capacity:
            return
        in_flight = self._in_flight.get(slot, 0)
        if in_flight > 0:
            self._capacity[slot] = 0
            logger.warning(
                "Model %s on %s: capacity zeroed with %d in-flight "
                "(deferred removal until drained)",
                model_id,
                gateway_id,
                in_flight,
            )
        else:
            del self._capacity[slot]
            self._in_flight.pop(slot, None)
            logger.info("Removed capacity: %s/%s", gateway_id, model_id)

    # ── Queries ──

    def available(self, gateway_id: str, model_id: str) -> int:
        """Return available slots for a (gateway, model) pair.

        Capacity minus in_flight.  Returns 0 when the slot is unknown
        or fully occupied.  Used by the
        DecisionEngine during feasibility evaluation to determine T0/T1 tier.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        return max(0, self._capacity.get(slot, 0) - self._in_flight.get(slot, 0))

    def get_slot_info(self, gateway_id: str, model_id: str) -> tuple[int, int, int]:
        """Return (available, in_flight, capacity) tuple for a (gateway, model) slot.

        Used by _try_immediate for ranking and by diagnostic endpoints.
        All values are 0 when the slot is unknown.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        capacity = self._capacity.get(slot, 0)
        in_flight = self._in_flight.get(slot, 0)
        return max(0, capacity - in_flight), in_flight, capacity

    def get_available_gateways(self, model_id: str) -> list[tuple[str, int]]:
        """Return gateways with available capacity, sorted descending.

        Returns ``[(gateway_id, available)]`` for all gateways with
        available > 0.  Used by routing to enumerate candidates for a model.
        Only includes slots where at least one request can be admitted immediately.
        """
        available_pairs: list[tuple[str, int]] = []
        for slot in self._capacity:
            if slot.model_id != model_id:
                continue
            available = self.available(slot.gateway_id, slot.model_id)
            if available > 0:
                available_pairs.append((slot.gateway_id, available))
        return sorted(available_pairs, key=lambda x: x[1], reverse=True)

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
            logger.info(
                "🔍 acquire_token: no immediate slot for %s/%s "
                "(allowed_gws=%s) — queueing. Snapshot: %s",
                request_id,
                model_id,
                list(allowed_gateway_ids),
                self.get_snapshot(),
            )
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
        """Try to reserve a slot immediately without queuing.

        Returns gateway_id on success, None if no capacity.  Ranks
        allowed gateways by available capacity (descending, random tiebreak)
        and increments in_flight on the best.  Called both from acquire_token
        (initial attempt) and _dispatch (queued waiter evaluation).
        """
        ranked: list[tuple[str, int]] = []
        for gw_id in allowed_gateway_ids:
            available, _in_flight, _capacity = self.get_slot_info(gw_id, model_id)
            if available > 0:
                ranked.append((gw_id, available))

        if not ranked:
            return None

        random.shuffle(ranked)
        ranked.sort(key=lambda item: -item[1])
        gw_id = ranked[0][0]
        slot = _Slot(gateway_id=gw_id, model_id=model_id)
        self._in_flight[slot] = self._in_flight.get(slot, 0) + 1
        logger.debug(f"Immediate admit: {request_id} → {gw_id}/{model_id}")
        return gw_id

    async def _wait_for_slot(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
        timeout_s: float | None,
    ) -> str:
        """Enqueue a waiter and block until a slot is assigned.  Returns gateway_id.

        Creates a Future, appends it to the per-model FIFO queue, and awaits
        resolution by _dispatch.  On timeout or cancellation, handles the race
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
        """Remove a waiter from its per-model queue by request_id.  Idempotent.

        Cancels the waiter's future if still pending and cleans up the queue
        entry.  Empty queues are removed from _queues to avoid memory growth.
        """
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
            asyncio.create_task(
                self._dispatch(model_id),
                name=f"capacity-recover-dispatch-{model_id}",
            )
        except RuntimeError as exc:
            logger.warning(
                "Failed to dispatch after slot leak recovery for %s: "
                "no running event loop (%s)",
                model_id,
                exc,
            )

    def _emit_slot_leak_recovered(
        self, request_id: str, gateway_id: str, model_id: str
    ) -> None:
        """Emit capacity.slot.leak.recovered canary signal via event bus.

        Any occurrence indicates the cancellation race in _wait_for_slot was hit
        under load: _dispatch resolved the future (incrementing in_flight) but the
        waiter's task was cancelled before a CapacityToken was created.  The slot
        was recovered by _recover_leaked_slot; this signal makes the recovery
        observable for monitoring.
        """
        if not self._event_bus:
            return
        try:
            from src.scheduling.events import CapacitySlotLeakRecovered

            event = CapacitySlotLeakRecovered(
                request_id=request_id,
                gateway_id=gateway_id,
                model_id=model_id,
                snapshot=self.get_snapshot(),
            )
            asyncio.create_task(self._event_bus.publish_async_nowait(event))
        except RuntimeError as exc:
            logger.warning(
                "Failed to emit slot leak recovered event for %s/%s: "
                "no running event loop (%s)",
                gateway_id,
                model_id,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Failed to emit slot leak recovered event for %s/%s: %s",
                gateway_id,
                model_id,
                exc,
                exc_info=True,
            )

    # ── Release ──

    async def _release(self, token: CapacityToken) -> None:
        """Return a capacity slot and dispatch waiting requests.

        Always invokes _dispatch even when in_flight is already 0 (e.g. after
        remove_model/remove_gateway race): the queue may have waiters that
        can be served on other gateways. Cleans up deferred zero-capacity
        slots when in_flight drains to zero.
        """
        slot = _Slot(gateway_id=token.gateway_id, model_id=token.model_id)
        in_flight = self._in_flight.get(slot, 0)

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
            self._in_flight[slot] = in_flight - 1
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

        current = self._in_flight.get(slot, 0)
        if current == 0 and self._capacity.get(slot, -1) == 0:
            del self._capacity[slot]
            del self._in_flight[slot]
            logger.info(
                "Deferred slot cleanup: %s/%s drained",
                token.gateway_id,
                token.model_id,
            )

        await self._dispatch(token.model_id)

    async def _dispatch(self, model_id: str) -> None:
        """Evaluate the full FIFO queue and assign capacity to serviceable waiters.

        Skips waiters whose allowed_gateway_ids have no available slots instead
        of stopping at the first unservable one (head-of-line blocking fix).
        Unservable waiters are retained in queue order for future dispatch calls.

        Handles cancelled waiters: Task.cancel() cancels the underlying future,
        so we skip them to avoid incrementing in_flight for unclaimed slots.
        Guards against the narrow race where cancellation arrives between
        _try_immediate (which increments in_flight) and set_result.
        """
        queue = self._queues.get(model_id)
        if not queue:
            return

        unservable: deque[_Waiter] = deque()
        dispatched = 0
        skipped = 0

        while queue:
            waiter = queue.popleft()

            if waiter.future.done():
                continue

            gw_id = self._try_immediate(
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
                self._in_flight[slot] = max(0, self._in_flight.get(slot, 0) - 1)
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
                self._in_flight[slot] = max(0, self._in_flight.get(slot, 0) - 1)
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
            self._queues[model_id] = unservable
        else:
            self._queues.pop(model_id, None)

    # ── Diagnostics ──

    def get_snapshot(self) -> dict[str, Any]:
        """Return a diagnostic snapshot of all capacity state as a plain dict.

        Includes per-slot capacity/in_flight, per-model queue contents with
        waiter request_ids and allowed gateways, and aggregate totals.  Used
        by health endpoints, logging, and the MCP manage_service status command.
        """
        return {
            "capacity": {str(s): c for s, c in self._capacity.items()},
            "in_flight": {str(s): c for s, c in self._in_flight.items()},
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
