"""Routing metrics and decision event signals.

Covers request routing, model load initiation/completion, token counting,
and routing decision observability.

Signals:
    request.routed — request successfully routed to gateway
    model.load.initiated — model load triggered
    model.load.completed — model load finished (success or failure)
    token.count.completed — token counting finished
    token.counting.failed — federated token counting failed
    scheduler.routing.decided — routing decision made by DecisionEngine
    scheduler.routing.failed — routing failed (no gateway found)
"""

from universal_event_bus import Event, event_factory

# ========================================
# Routing Metrics Event Signals (UDP-emitted metrics)
# ========================================

REQUEST_ROUTED = "request.routed"
"""
Request successfully routed to gateway
Emitted when a request is routed to a specific gateway for processing.
This metric is useful for tracking routing decisions and load distribution.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "routing_time_ms": float,  # Time taken to route request
    "queue_position": Optional[int],  # Position in queue if queued
    "immediate_route": bool  # True if routed immediately, False if queued
}
"""

MODEL_LOAD_INITIATED = "model.load.initiated"
"""
Model loading initiated on gateway
Emitted when a model load operation is triggered.

Payload: {
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "already_loaded": bool,
    "request_id": Optional[str]  # Present when triggered by request
}
"""

MODEL_LOAD_COMPLETED = "model.load.completed"
"""
Model loading completed on gateway
Emitted when a model load operation finishes (success or failure).

Payload: {
    "model_id": str,
    "gateway_url": str,
    "gateway_name": str,
    "timestamp": float,
    "success": bool,
    "load_time_ms": float,
    "error": Optional[str],
    "request_id": Optional[str]  # Present when triggered by request
}
"""

TOKEN_COUNT_COMPLETED = "token.count.completed"
"""
Token counting completed
Emitted when a token counting operation completes (success or failure).

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_url": str,
    "timestamp": float,
    "success": bool,
    "count_time_ms": float,
    "input_tokens": Optional[int],
    "context_limit": Optional[int],
    "allocated_max_tokens": Optional[int],
    "error": Optional[str]
}
"""

TOKEN_COUNTING_FAILED = "token.counting.failed"
"""
Federated token counting failed due to infrastructure issue.
Emitted when the gateway/edge container is unreachable.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "error": str
}
"""

# ========================================
# Routing Decision Event Signals
# ========================================

ROUTING_DECISION = "scheduler.routing.decided"
"""
Routing decision made by DecisionEngine.
Emitted for every routing decision with full observability.

Payload: {
    "model_id": str,                    # Model being routed
    "original_model_id": str | None,    # Original request model ID
    "selected_gateway": str | None,     # Selected gateway name
    "selection_reason": str,            # Why this gateway was selected
    "selection_tier": str | None,       # T0/T1/T2 tier name
    "candidate_count": int,             # Total candidates evaluated
    "feasible_count": int,              # Number of feasible candidates
    "evaluation_time_ms": float,        # Time spent in decision engine
    "request_id": str | None,           # Proxy request ID for tracing
    "timestamp": float,                 # Unix timestamp

    # Optional detailed trace (expensive, enable for debugging)
    "candidates": list[dict] | None,    # Full candidate details
}
"""

ROUTING_DECISION_FAILED = "scheduler.routing.failed"
"""
Routing decision failed - no gateway available.
Emitted when no feasible gateway can be found.

Payload: {
    "model_id": str,
    "original_model_id": str | None,
    "candidate_count": int,
    "evaluation_time_ms": float,
    "request_id": str | None,
    "timestamp": float,
    "reason": str
}
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def RequestRouted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    routing_time_ms: float,
    queue_position: int | None = None,
    immediate_route: bool = True,
) -> Event:
    """
    Create REQUEST_ROUTED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model being routed
        gateway_url: Selected gateway URL
        gateway_name: Selected gateway name
        timestamp: Unix timestamp
        routing_time_ms: Time taken to route request
        queue_position: Position in queue if queued
        immediate_route: True if routed immediately, False if queued

    Returns:
        Event with RequestRouted signal
    """
    return Event(
        signal=REQUEST_ROUTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "gateway_name": gateway_name,
            "timestamp": timestamp,
            "routing_time_ms": routing_time_ms,
            "queue_position": queue_position,
            "immediate_route": immediate_route,
        },
    )


@event_factory
def ModelLoadInitiated(
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    already_loaded: bool = False,
    *,
    request_id: str | None = None,
) -> Event:
    """
    Create MODEL_LOAD_INITIATED event.

    INVARIANT: request_id present when triggered by request

    Args:
        model_id: Model being loaded
        gateway_url: Target gateway URL
        gateway_name: Target gateway name
        timestamp: Unix timestamp
        already_loaded: True if model was already loaded
        request_id: Proxy request ID (when triggered by request)

    Returns:
        Event with ModelLoadInitiated signal
    """
    payload = {
        "model_id": model_id,
        "gateway_url": gateway_url,
        "gateway_name": gateway_name,
        "timestamp": timestamp,
        "already_loaded": already_loaded,
    }
    if request_id:
        payload["request_id"] = request_id
    return Event(signal=MODEL_LOAD_INITIATED, payload=payload)


@event_factory
def ModelLoadCompleted(
    model_id: str,
    gateway_url: str,
    gateway_name: str,
    timestamp: float,
    success: bool,
    load_time_ms: float,
    error: str | None = None,
    *,
    request_id: str | None = None,
) -> Event:
    """
    Create MODEL_LOAD_COMPLETED event.

    INVARIANT: request_id present when triggered by request

    Args:
        model_id: Model that finished loading
        gateway_url: Gateway URL
        gateway_name: Gateway name
        timestamp: Unix timestamp
        success: True if load succeeded
        load_time_ms: Time taken to load model
        error: Error message if failed
        request_id: Proxy request ID (when triggered by request)

    Returns:
        Event with ModelLoadCompleted signal
    """
    payload = {
        "model_id": model_id,
        "gateway_url": gateway_url,
        "gateway_name": gateway_name,
        "timestamp": timestamp,
        "success": success,
        "load_time_ms": load_time_ms,
        "error": error,
    }
    if request_id:
        payload["request_id"] = request_id
    return Event(signal=MODEL_LOAD_COMPLETED, payload=payload)


@event_factory
def TokenCountCompleted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    timestamp: float,
    success: bool,
    count_time_ms: float,
    input_tokens: int | None = None,
    context_limit: int | None = None,
    allocated_max_tokens: int | None = None,
    error: str | None = None,
) -> Event:
    """
    Create TOKEN_COUNT_COMPLETED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model for token counting
        gateway_url: Gateway URL
        timestamp: Unix timestamp
        success: True if count succeeded
        count_time_ms: Time taken for token counting
        input_tokens: Number of input tokens
        context_limit: Model context limit
        allocated_max_tokens: Allocated max_tokens value
        error: Error message if failed

    Returns:
        Event with TokenCountCompleted signal
    """
    return Event(
        signal=TOKEN_COUNT_COMPLETED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "timestamp": timestamp,
            "success": success,
            "count_time_ms": count_time_ms,
            "input_tokens": input_tokens,
            "context_limit": context_limit,
            "allocated_max_tokens": allocated_max_tokens,
            "error": error,
        },
    )


@event_factory
def TokenCountingFailed(
    request_id: str,
    model_id: str,
    gateway_id: str,
    error: str,
) -> Event:
    """
    Create TOKEN_COUNTING_FAILED event.

    Emitted when federated token counting fails due to an infrastructure
    issue (gateway unreachable, edge container down, etc.).

    Args:
        request_id: Proxy request ID
        model_id: Model being requested
        gateway_id: Gateway that failed token counting
        error: Error description

    Returns:
        Event with TokenCountingFailed signal
    """
    return Event(
        signal=TOKEN_COUNTING_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "error": error,
        },
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
        selection_reason: Why this gateway was selected
        candidate_count: Total candidates evaluated
        feasible_count: Number of feasible candidates
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        original_model_id: Original request model ID
        selected_gateway: Selected gateway name
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
        model_id: Model that failed to route
        candidate_count: Total candidates evaluated
        evaluation_time_ms: Time spent in decision engine
        timestamp: Unix timestamp
        reason: Failure reason
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
