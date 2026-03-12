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


async def wait_for_capacity_signal(
    event_bus: EventBus,
    model_id: str,
    request_id: str,
    deadline: float,
) -> bool:
    """Wait for a single gateway.resource.updated signal.

    Returns True if a signal was received, False on timeout.
    Caller should re-evaluate feasibility after return.
    """
    del model_id, request_id  # logging context reserved for future diagnostics

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
        sub = event_bus.subscribe_async(RESOURCE_UPDATED_SIGNAL, _on_resource_updated)
        await asyncio.wait_for(wakeup.wait(), timeout=wait_time)
        return True
    except TimeoutError:
        return False
    finally:
        if sub is not None:
            sub.unsubscribe()


def extract_retryable_constraint(trace: Any) -> str | None:
    """Extract the first retryable constraint name from a decision trace."""
    candidates = getattr(trace, "candidates", None)
    if not candidates:
        return None

    for candidate in candidates:
        failures = getattr(candidate, "constraints_failed", ())
        for failure in failures:
            details = getattr(failure, "details", None) or {}
            if details.get("retryable"):
                return getattr(failure, "constraint", None)
    return None
