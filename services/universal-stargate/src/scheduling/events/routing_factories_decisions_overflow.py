"""Stargate scheduling routing events — split module covering routing decisions and overflow handling: queuing, dequeuing, decision success/failure, timeout, overflow trigger/failure, overflow capacity-assigned, and model-load-overflow-started signals, built as `Event` objects for the routing scheduler."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_decisions import (
    MODEL_CAPACITY_OVERFLOW_ASSIGNED,
    MODEL_LOAD_OVERFLOW_STARTED,
    ROUTING_DECISION,
    ROUTING_DECISION_FAILED,
    ROUTING_DEQUEUED,
    ROUTING_OVERFLOW_FAILED,
    ROUTING_OVERFLOW_TRIGGERED,
    ROUTING_QUEUED,
    ROUTING_TIMEOUT,
)


@event_factory
def RoutingDecision(
    model_id: str,
    selection_reason: str,
    candidate_count: int,
    feasible_count: int,
    evaluation_time_ms: float,
    timestamp: float,
    original_model_id: str | None = None,
    selected_gateway: str | None = None,
    selection_tier: str | None = None,
    request_id: str | None = None,
    candidates: list[dict] | None = None,
) -> Event:
    """
    Create ROUTING_DECISION event.

    Args:
        model_id: Model being routed
        selection_reason: Why this gateway was selected or why no gateway was
            selected on this attempt
        candidate_count: Total candidates evaluated
        feasible_count: Number of feasible candidates
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        original_model_id: Original request model ID
        selected_gateway: Selected gateway name, or None when this attempt had
            no feasible gateway
        selection_tier: T0/T1/T2 tier name
        request_id: Proxy request ID for tracing
        candidates: Full candidate details (optional, expensive)

    Returns:
        Event with RoutingDecision signal
    """
    return Event(
        signal=ROUTING_DECISION,
        payload={
            "model_id": model_id,
            "original_model_id": original_model_id,
            "selected_gateway": selected_gateway,
            "selection_reason": selection_reason,
            "selection_tier": selection_tier,
            "candidate_count": candidate_count,
            "feasible_count": feasible_count,
            "evaluation_time_ms": evaluation_time_ms,
            "request_id": request_id,
            "timestamp": timestamp,
            "candidates": candidates,
        },
    )


@event_factory
def RoutingDecisionFailed(
    model_id: str,
    candidate_count: int,
    evaluation_time_ms: float,
    timestamp: float,
    reason: str,
    original_model_id: str | None = None,
    request_id: str | None = None,
) -> Event:
    """
    Create ROUTING_DECISION_FAILED event.

    Args:
        model_id: Model that failed to route terminally
        candidate_count: Total candidates evaluated
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        reason: Terminal failure reason after retryable waits were exhausted
        original_model_id: Original request model ID
        request_id: Proxy request ID for tracing

    Returns:
        Event with RoutingDecisionFailed signal
    """
    return Event(
        signal=ROUTING_DECISION_FAILED,
        payload={
            "model_id": model_id,
            "original_model_id": original_model_id,
            "candidate_count": candidate_count,
            "evaluation_time_ms": evaluation_time_ms,
            "request_id": request_id,
            "timestamp": timestamp,
            "reason": reason,
        },
    )


@event_factory
def RoutingQueued(
    request_id: str,
    model_id: str,
    constraint: str,
    timestamp: float,
    gateway_id: str | None = None,
) -> Event:
    """Emit when a request is queued waiting for a retryable routing condition."""
    return Event(
        signal=ROUTING_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "constraint": constraint,
            "gateway_id": gateway_id,
            "timestamp": timestamp,
        },
    )


@event_factory
def RoutingDequeued(
    request_id: str,
    model_id: str,
    gateway_id: str,
    wait_ms: float,
    timestamp: float,
) -> Event:
    """Emit when a queued request is dequeued and assigned to a gateway."""
    return Event(
        signal=ROUTING_DEQUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "wait_ms": wait_ms,
            "timestamp": timestamp,
        },
    )


@event_factory
def RoutingTimeout(
    request_id: str,
    model_id: str,
    constraint: str,
    wait_ms: float,
    timestamp: float,
) -> Event:
    """Emit when a queued request exceeds wait timeout for its constraint."""
    return Event(
        signal=ROUTING_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "constraint": constraint,
            "wait_ms": wait_ms,
            "timestamp": timestamp,
        },
    )


@event_factory
def RoutingOverflowTriggered(
    request_id: str,
    model_id: str,
    from_gateway: str,
    to_gateway: str,
    reason: str,
) -> Event:
    """Emit when non-sticky overflow reroutes from one gateway to another."""
    return Event(
        signal=ROUTING_OVERFLOW_TRIGGERED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "from_gateway": from_gateway,
            "to_gateway": to_gateway,
            "reason": reason,
        },
    )


@event_factory
def RoutingOverflowFailed(
    request_id: str,
    model_id: str,
    tried_gateways: list[str],
    reason: str,
) -> Event:
    """Emit when a failed overflow attempt is part of the terminal routing path."""
    return Event(
        signal=ROUTING_OVERFLOW_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "tried_gateways": tried_gateways,
            "reason": reason,
        },
    )


@event_factory
def ModelCapacityOverflowAssigned(
    request_id: str,
    model_id: str,
    from_gateway: str,
    to_gateway: str,
    depth_before: int,
) -> Event:
    """Emit when a model request is overflow-assigned due to gateway capacity."""
    return Event(
        signal=MODEL_CAPACITY_OVERFLOW_ASSIGNED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "from_gateway": from_gateway,
            "to_gateway": to_gateway,
            "depth_before": depth_before,
        },
    )


@event_factory
def ModelLoadOverflowStarted(
    request_id: str,
    model_id: str,
    gateway_id: str,
    reason: str,
) -> Event:
    """Emit when overflow path starts cold-loading a model on another gateway."""
    return Event(
        signal=MODEL_LOAD_OVERFLOW_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "reason": reason,
        },
    )
