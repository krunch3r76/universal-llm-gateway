"""
Event-driven capacity pool with self-releasing tokens.

Replaces CapacityLedger + AdmissionQueue + CapacityReleaseConsumer.

INVARIANT: ∀ acquire() ⟹ release() via CapacityToken.__aexit__
INVARIANT: ∀ slot: in_flight[slot] ≥ 0
INVARIANT: token release is idempotent (guard prevents double-release)
INVARIANT: ∀ dispatch-admitted waiter cancelled before token creation ⟹
           in_flight decremented and next waiter dispatched (no leaked slots)
INVARIANT: ∀ paused model m: admission suppressed until deadline; in-flight
           drains naturally; queued waiters remain in FIFO order and wake
           on resume. The pause is the preemption primitive the scheduler
           uses to resolve starvation against a continuously-busy model.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from ._acquisition import _AcquisitionMixin
from ._bookkeeping import _BookkeepingMixin
from ._cancellation import _CancellationMixin
from ._diagnostics import _DiagnosticsMixin
from ._events_admission import _EventsAdmissionMixin
from ._events_queue import _EventsQueueMixin
from ._pause import _PauseMixin
from ._release_dispatch import _ReleaseDispatchMixin
from ._types import _EventBusLike

logger = get_logger(__name__)


class CapacityPool(
    _BookkeepingMixin,
    _AcquisitionMixin,
    _ReleaseDispatchMixin,
    _PauseMixin,
    _CancellationMixin,
    _EventsQueueMixin,
    _EventsAdmissionMixin,
    _DiagnosticsMixin,
):
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

    def __init__(
        self,
        event_bus: _EventBusLike | None = None,
        max_queue_depth: int = 0,
    ) -> None:
        self._event_bus = event_bus
        self._max_queue_depth = max(0, int(max_queue_depth))
        self._capacity: dict[Any, int] = {}
        self._in_flight: dict[Any, int] = {}
        self._queues: dict[str, Any] = {}
        self._subscribed = False
        self._paused_until: dict[str, float] = {}
        self._paused_reason: dict[str, str] = {}
        self._resume_tasks: dict[str, asyncio.Task[None]] = {}

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
