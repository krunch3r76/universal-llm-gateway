"""
Pre-routing capacity primitives.

Demand registry, retryable constraint extraction, and the queue timeout budget.
Wait logic itself lives in pre_route_queue.py (uses federated state changes).
"""

from __future__ import annotations

from typing import Any

from .constraint_retryable import extract_retryable_constraint

QUEUE_TIMEOUT_S: float = 120.0
"""Total time budget for pre-routing wait (seconds)."""

# ---------------------------------------------------------------------------
# Demand registry — tracks routing keys with active waiters.
# Asyncio-safe (single event loop), no locks needed.
# ---------------------------------------------------------------------------

_demand: dict[str, set[str]] = {}  # routing_key → {request_ids}


def register_demand(routing_key: str, request_id: str) -> None:
    """Register that `request_id` is actively waiting for capacity on `routing_key`.

    Informs the eviction planner not to evict models with queued consumers.
    """
    _demand.setdefault(routing_key, set()).add(request_id)


def unregister_demand(routing_key: str, request_id: str) -> None:
    """Remove `request_id` from the waiters for `routing_key`."""
    if routing_key in _demand:
        _demand[routing_key].discard(request_id)
        if not _demand[routing_key]:
            del _demand[routing_key]


def has_demand_for(routing_key: str) -> bool:
    """True if at least one request is waiting for `routing_key`."""
    return bool(_demand.get(routing_key))


def count_demand_for(routing_key: str) -> int:
    """Number of requests currently waiting for capacity on `routing_key`."""
    return len(_demand.get(routing_key, set()))


# Re-export for pre_route_queue and tests.
__all__ = [
    "QUEUE_TIMEOUT_S",
    "count_demand_for",
    "extract_retryable_constraint",
    "has_demand_for",
    "register_demand",
    "unregister_demand",
]
