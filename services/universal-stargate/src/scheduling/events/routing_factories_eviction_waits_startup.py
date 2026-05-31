"""Stargate scheduling routing events — split module"
"(routing_factories_eviction_waits_startup.py)."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_routing_waits import (
    ROUTING_DRAIN_INITIATED,
    ROUTING_EVICTION_WAIT_CANCELLED,
    ROUTING_EVICTION_WAIT_RESOLVED,
    ROUTING_EVICTION_WAIT_STARTED,
    ROUTING_EVICTION_WAIT_TIMEOUT,
    ROUTING_STARTUP_QUEUED,
    ROUTING_STARTUP_RESOLVED,
    ROUTING_STARTUP_TIMEOUT,
)


@event_factory
def RoutingEvictionWaitStarted(
    request_id: str,
    model_id: str,
    timeout_s: float,
    queue_depth: int,
) -> Event:
    """Emit when request enters eviction wait queue (transient eviction blocked)."""
    return Event(
        signal=ROUTING_EVICTION_WAIT_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "timeout_s": timeout_s,
            "queue_depth": queue_depth,
        },
    )


@event_factory
def RoutingEvictionWaitResolved(
    request_id: str,
    model_id: str,
    gateway_id: str,
    waited_ms: int,
) -> Event:
    """Emit when eviction wait completed and selection succeeded."""
    return Event(
        signal=ROUTING_EVICTION_WAIT_RESOLVED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "waited_ms": waited_ms,
        },
    )


@event_factory
def RoutingEvictionWaitTimeout(
    request_id: str,
    model_id: str,
    waited_ms: int,
    exit_reason: str,
    exit_constraint_summary: list[dict],
) -> Event:
    """Emit when the eviction wait exits without a resolved placement.

    exit_reason ∈ {"budget_exhausted", "non_transient"}. See signal docstring
    for semantics.
    """
    return Event(
        signal=ROUTING_EVICTION_WAIT_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "waited_ms": waited_ms,
            "exit_reason": exit_reason,
            "exit_constraint_summary": exit_constraint_summary,
        },
    )


@event_factory
def RoutingDrainInitiated(
    request_id: str,
    target_model_id: str,
    gateway_ids: list[str],
    drained_model_ids: list[str],
    duration_s: float,
    starved_for_ms: int,
) -> Event:
    """Emit when starvation-triggered admission drain begins."""
    return Event(
        signal=ROUTING_DRAIN_INITIATED,
        payload={
            "request_id": request_id,
            "target_model_id": target_model_id,
            "gateway_ids": gateway_ids,
            "drained_model_ids": drained_model_ids,
            "duration_s": duration_s,
            "starved_for_ms": starved_for_ms,
        },
    )


@event_factory
def RoutingEvictionWaitCancelled(
    request_id: str,
    model_id: str,
    waited_ms: int,
) -> Event:
    """Emit when eviction wait was cancelled (client disconnect / task cancel)."""
    return Event(
        signal=ROUTING_EVICTION_WAIT_CANCELLED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "waited_ms": waited_ms,
        },
    )


@event_factory
def RoutingStartupQueued(
    request_id: str,
    model_id: str,
    uptime_s: float,
    timeout_s: float,
) -> Event:
    """Emit when a request is held during startup window (no gateways yet)."""
    return Event(
        signal=ROUTING_STARTUP_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "uptime_s": uptime_s,
            "timeout_s": timeout_s,
        },
    )


@event_factory
def RoutingStartupResolved(
    request_id: str,
    model_id: str,
    gateway_id: str,
    waited_ms: int,
    uptime_s: float,
) -> Event:
    """Emit when startup-queued request unblocks after a gateway connects."""
    return Event(
        signal=ROUTING_STARTUP_RESOLVED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "waited_ms": waited_ms,
            "uptime_s": uptime_s,
        },
    )


@event_factory
def RoutingStartupTimeout(
    request_id: str,
    model_id: str,
    waited_ms: int,
    uptime_s: float,
) -> Event:
    """Emit when startup queue window exhausted with no gateway connecting."""
    return Event(
        signal=ROUTING_STARTUP_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "waited_ms": waited_ms,
            "uptime_s": uptime_s,
        },
    )
