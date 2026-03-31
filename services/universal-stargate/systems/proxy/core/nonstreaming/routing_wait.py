"""
Pre-routing capacity primitives.

Demand registry, retryable constraint extraction, and the queue timeout budget.
Wait logic itself lives in pre_route_queue.py (uses federated state changes).
"""

from __future__ import annotations

from typing import Any

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


def extract_retryable_constraint(trace: Any) -> str | None:
    """Extract the first queueable capacity constraint from a decision trace.

    Sticky-capacity requests should wait whenever the failure is transient.
    Most transient constraints mark ``details.retryable=True`` directly, but
    resource failures only become transient when the same candidate also reports
    ``can_fit_with_eviction``. Keep the queue gate aligned with terminal
    ``STICKY_CAPACITY`` classification so retryable requests do not fail fast.
    """
    for candidate in getattr(trace, "candidates", ()):
        failures = tuple(getattr(candidate, "constraints_failed", ()))
        failed_constraints = {
            getattr(failure, "constraint", None) for failure in failures
        }

        for failure in failures:
            details = getattr(failure, "details", None) or {}
            if details.get("retryable"):
                return getattr(failure, "constraint", None)

        if "can_fit_with_eviction" in failed_constraints:
            for resource_constraint in ("has_enough_vram", "has_enough_ram"):
                if resource_constraint in failed_constraints:
                    return resource_constraint

    return None
