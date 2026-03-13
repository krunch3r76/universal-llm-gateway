"""
Signal-driven wait for pre-routing capacity.

When routing fails with a retryable constraint (e.g. eviction blocked by
busy models), wait for a gateway.resource.updated signal instead of failing
immediately and relying on the chat retry loop.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from universal_event_bus import EventBus, Subscription

QUEUE_TIMEOUT_S: float = 120.0
"""Total time budget for pre-routing wait (seconds)."""

SIGNAL_WAIT_S: float = 5.0
"""Per-signal wait bound (seconds)."""

RESOURCE_UPDATED_SIGNAL = "gateway.resource.updated"

# ---------------------------------------------------------------------------
# Demand registry — tracks routing keys with active waiters.
# Asyncio-safe (single event loop), no locks needed.
# ---------------------------------------------------------------------------

_demand: dict[str, set[str]] = {}  # routing_key → {request_ids}


def register_demand(routing_key: str, request_id: str) -> None:
    """Registers that `request_id` is actively waiting for capacity on `routing_key`.
    
    This informs the eviction planner not to evict models associated with this key
    while requests are queued. `routing_key` typically corresponds to a model_id.
    """
    _demand.setdefault(routing_key, set()).add(request_id)


def unregister_demand(routing_key: str, request_id: str) -> None:
    """Removes `request_id` from the set of waiters for `routing_key`.
    
    If no other requests are waiting for `routing_key`, the entry is removed
    from the demand registry. This should be called after a wait completes or times out.
    """
    if routing_key in _demand:
        _demand[routing_key].discard(request_id)
        if not _demand[routing_key]:
            del _demand[routing_key]


def has_demand_for(routing_key: str) -> bool:
    """Returns True if there is at least one request currently waiting for `routing_key`.
    
    This indicates that a model associated with `routing_key` should not be evicted.
    """
    return bool(_demand.get(routing_key))


def count_demand_for(routing_key: str) -> int:
    """Returns the number of requests currently waiting for capacity on `routing_key`.
    
    This count reflects the active demand for a specific model or resource.
    """
    return len(_demand.get(routing_key, set()))


async def wait_for_capacity_signal(
    event_bus: EventBus,
    model_id: str,
    request_id: str,
    deadline: float,
) -> bool:
    """Wait for a single gateway.resource.updated signal.

    Returns True if a signal was received, False on timeout.
    Caller should re-evaluate feasibility after return.

    Registers demand while waiting so the eviction planner can
    avoid evicting models that have queued consumers.
    """
    routing_key = model_id

    register_demand(routing_key, request_id)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        wait_time = min(SIGNAL_WAIT_S, remaining)
        wakeup = asyncio.Event()
        sub: Subscription | None = None

        async def _on_resource_updated(event: Any) -> None:
            del event
            wakeup.set()

        try:
            sub = event_bus.subscribe_async(
                RESOURCE_UPDATED_SIGNAL, _on_resource_updated
            )
            await asyncio.wait_for(wakeup.wait(), timeout=wait_time)
            return True
        except TimeoutError:
            return False
        finally:
            if sub is not None:
                sub.unsubscribe()
    finally:
        unregister_demand(routing_key, request_id)


def extract_retryable_constraint(trace: Any) -> str | None:
    """Extract the first retryable constraint name from a decision trace."""
    for candidate in getattr(trace, "candidates", ()): # Iterate directly, default to empty tuple
        for failure in getattr(candidate, "constraints_failed", ()): # Iterate directly
            details = getattr(failure, "details", None) or {}
            if details.get("retryable"):
                return getattr(failure, "constraint", None)
    return None
