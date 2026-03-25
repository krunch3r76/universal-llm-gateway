"""Routing metrics and decision event signals for Stargate scheduling.

Covers request routing, model load initiation/completion, token counting,
and routing decision observability.

Signals:
    request.routed — request successfully routed to gateway
    model.load.initiated — model load triggered
    model.load.completed — model load finished (success or failure)
    model.load.overflow.started — overflow gateway cold-load initiated
    model.capacity.overflow.assigned — admission moved request to overflow target
    token.count.completed — token counting finished
    token.counting.failed — federated token counting failed
    scheduler.routing.decided — routing decision made by DecisionEngine
    scheduler.routing.failed — routing failed (permanent no-feasible-gateway)
    scheduler.routing.queued — request entered pre-routing wait queue
    scheduler.routing.dequeued — request admitted after pre-routing wait
    scheduler.routing.timeout — pre-routing wait budget expired
    routing.overflow.triggered — overflow branch selected alternate gateway
    routing.overflow.failed — overflow branch failed to assign feasible target
    scheduler.eviction.cooldown.blocked — escape hatch: all candidates protected
    scheduler.eviction.cooldown.applied — cooldown filtered eviction candidates
    scheduler.eviction.demand.applied — demand protected eviction candidates
    routing.eviction.wait.started — request entered eviction wait queue
    routing.eviction.wait.resolved — wait completed, selection succeeded
    routing.eviction.wait.timeout — eviction wait timed out
    routing.eviction.wait.cancelled — wait cancelled (client disconnect)
    routing.startup.queued — request held during startup window (no gateways yet)
    routing.startup.resolved — startup-queued request unblocked after gateway connects
    routing.startup.timeout — startup queue window exhausted, no gateway appeared
"""

# ruff: noqa: N802

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

GATEWAY_VRAM_PHANTOM_DETECTED = "gateway.vram.phantom.detected"
"""
Forwarded from Gateway when hardware VRAM usage exceeds tracked model VRAM.

Payload: {
    "gateway_id": str,
    "hardware_used_mb": int,
    "catalog_used_mb": int,
    "discrepancy_mb": int,
    "tracked_models": list[str],
}
"""

GATEWAY_PHANTOM_MODEL_DETECTED = "gateway.model.phantom.detected"
"""
Forwarded from Gateway when a running worker is not tracked as LOADED/BUSY.

Payload: {
    "gateway_id": str,
    "model_id": str,
    "process_status": str,
    "tracker_status": str | None,
}
"""

GATEWAY_PHANTOM_MODEL_CLEANED = "gateway.model.phantom.cleaned"
"""
Forwarded from Gateway after phantom cleanup attempt.

Payload: {
    "gateway_id": str,
    "model_id": str,
    "success": bool,
    "vram_freed_mb": int | None,
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
Routing decision failed - PERMANENT failure.
Emitted when no feasible gateway can be found without retryable capacity wait,
or when pre-routing queue timeout expires.

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

ROUTING_QUEUED = "scheduler.routing.queued"
"""
Request entered pre-routing wait queue (retryable constraint).
Emitted once when a request begins waiting for capacity signal.

Payload: {
    "request_id": str,
    "model_id": str,
    "constraint": str,
    "gateway_id": str | None,
    "timestamp": float
}
"""

ROUTING_DEQUEUED = "scheduler.routing.dequeued"
"""
Request admitted after waiting for capacity signal.
Emitted when gateway.resource.updated unblocks a queued request.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "wait_ms": float,
    "timestamp": float
}
"""

ROUTING_TIMEOUT = "scheduler.routing.timeout"
"""
Pre-routing queue timeout expired before capacity became available.

Payload: {
    "request_id": str,
    "model_id": str,
    "constraint": str,
    "wait_ms": float,
    "timestamp": float
}
"""

ROUTING_OVERFLOW_TRIGGERED = "routing.overflow.triggered"
"""
Non-sticky overflow branch selected an alternate gateway.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "to_gateway": str,
    "reason": str
}
"""

ROUTING_OVERFLOW_FAILED = "routing.overflow.failed"
"""
Non-sticky overflow branch failed to find or load a feasible alternate gateway.

Payload: {
    "request_id": str,
    "model_id": str,
    "tried_gateways": list[str],
    "reason": str
}
"""

MODEL_CAPACITY_OVERFLOW_ASSIGNED = "model.capacity.overflow.assigned"
"""
Admission assigned request to overflow gateway after spillover decision.

Payload: {
    "request_id": str,
    "model_id": str,
    "from_gateway": str,
    "to_gateway": str,
    "depth_before": int
}
"""

MODEL_LOAD_OVERFLOW_STARTED = "model.load.overflow.started"
"""
Cold-load started on overflow gateway chosen by spillover.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
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
    """Emit when overflow reroute cannot find a valid alternate gateway."""
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


# ========================================
# Eviction Hysteresis Event Signals
# ========================================

EVICTION_COOLDOWN_BLOCKED = "scheduler.eviction.cooldown.blocked"
"""
All evictable candidates were protected (cooldown and/or demand).
Escape hatch activated: least-harmful candidate evicted.

Payload: {
    "request_id": str | None,
    "model_id": str,
    "gateway_id": str,
    "evicted_model_id": str,
    "escape_reason": str,      # "cooldown" | "demand"
    "cooldown_remaining_s": float | None,
    "candidates_in_cooldown": int,
    "candidates_demand_protected": int,
    "timestamp": float
}
"""

EVICTION_COOLDOWN_APPLIED = "scheduler.eviction.cooldown.applied"
"""
Eviction planner filtered candidates by cooldown — informational.
Emitted when ≥1 model was protected by cooldown during eviction planning.

Payload: {
    "model_id": str,
    "gateway_id": str,
    "protected_count": int,
    "cooldown_s": float,
    "timestamp": float
}
"""

EVICTION_DEMAND_APPLIED = "scheduler.eviction.demand.applied"
"""
Eviction planner filtered candidates by demand protection — informational.
Emitted when ≥1 model was protected by routing queue demand.

Payload: {
    "model_id": str,
    "gateway_id": str,
    "protected_count": int,
    "waiter_counts": dict[str, int],
    "timestamp": float
}
"""

# ========================================
# Eviction wait queue (pre-selection queue)
# ========================================

ROUTING_EVICTION_WAIT_STARTED = "routing.eviction.wait.started"
"""
Request entered eviction wait queue (transient eviction_blocked_by_busy_models).

Payload: {
    "request_id": str,
    "model_id": str,
    "timeout_s": float,
    "queue_depth": int
}
"""

ROUTING_EVICTION_WAIT_RESOLVED = "routing.eviction.wait.resolved"
"""
Wait completed; selection succeeded after state change.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "waited_ms": int
}
"""

ROUTING_EVICTION_WAIT_TIMEOUT = "routing.eviction.wait.timeout"
"""
Eviction wait timed out before capacity became available.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int
}
"""

ROUTING_EVICTION_WAIT_CANCELLED = "routing.eviction.wait.cancelled"
"""
Client disconnected or task cancelled during eviction wait.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int
}
"""

ROUTING_STARTUP_QUEUED = "routing.startup.queued"
"""
Request queued during startup window because no gateways have connected yet.

Emitted when Stargate is within its startup_queue_timeout_s window and the request
is held rather than immediately rejected with GATEWAY_DISCONNECTED.

Payload: {
    "request_id": str,
    "model_id": str,
    "uptime_s": float,
    "timeout_s": float
}
"""

ROUTING_STARTUP_RESOLVED = "routing.startup.resolved"
"""
Startup-queued request unblocked after a gateway connected.

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "waited_ms": int,
    "uptime_s": float
}
"""

ROUTING_STARTUP_TIMEOUT = "routing.startup.timeout"
"""
Startup queue timed out with no gateway connecting before deadline.

Payload: {
    "request_id": str,
    "model_id": str,
    "waited_ms": int,
    "uptime_s": float
}
"""


@event_factory
def EvictionCooldownBlocked(
    model_id: str,
    gateway_id: str,
    evicted_model_id: str,
    escape_reason: str,
    timestamp: float,
    request_id: str | None = None,
    cooldown_remaining_s: float | None = None,
    candidates_in_cooldown: int = 0,
    candidates_demand_protected: int = 0,
) -> Event:
    """Emit when eviction uses escape hatch because all candidates were protected."""
    return Event(
        signal=EVICTION_COOLDOWN_BLOCKED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicted_model_id": evicted_model_id,
            "escape_reason": escape_reason,
            "cooldown_remaining_s": cooldown_remaining_s,
            "candidates_in_cooldown": candidates_in_cooldown,
            "candidates_demand_protected": candidates_demand_protected,
            "timestamp": timestamp,
        },
    )


@event_factory
def EvictionCooldownApplied(
    model_id: str,
    gateway_id: str,
    protected_count: int,
    cooldown_s: float,
    timestamp: float,
) -> Event:
    """Emit when cooldown protection filtered one or more eviction candidates."""
    return Event(
        signal=EVICTION_COOLDOWN_APPLIED,
        payload={
            "model_id": model_id,
            "gateway_id": gateway_id,
            "protected_count": protected_count,
            "cooldown_s": cooldown_s,
            "timestamp": timestamp,
        },
    )


@event_factory
def EvictionDemandApplied(
    model_id: str,
    gateway_id: str,
    protected_count: int,
    waiter_counts: dict[str, int],
    timestamp: float,
) -> Event:
    """Emit when demand protection filtered one or more eviction candidates."""
    return Event(
        signal=EVICTION_DEMAND_APPLIED,
        payload={
            "model_id": model_id,
            "gateway_id": gateway_id,
            "protected_count": protected_count,
            "waiter_counts": waiter_counts,
            "timestamp": timestamp,
        },
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
) -> Event:
    """Emit when eviction wait timed out."""
    return Event(
        signal=ROUTING_EVICTION_WAIT_TIMEOUT,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "waited_ms": waited_ms,
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


# ========================================
# OOM Recovery Signals
# ========================================

ROUTING_INFERENCE_OOM_RECOVERY_STARTED = "routing.inference.oom.recovery.started"
"""
OOM recovery initiated: evicting idle models after inference 500.
Payload: request_id, model_id, gateway_id, evicting_count, evicting_models
"""

ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED = "routing.inference.oom.recovery.succeeded"
"""
OOM recovery succeeded: retry after eviction returned a non-500 response.
Payload: request_id, model_id, gateway_id, evicted_count
"""



@event_factory
def OomRecoveryStarted(
    request_id: str,
    model_id: str,
    gateway_id: str,
    evicting_count: int,
    evicting_models: list[str],
) -> Event:
    """Emit when OOM recovery begins (evicting idle models)."""
    return Event(
        signal=ROUTING_INFERENCE_OOM_RECOVERY_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicting_count": evicting_count,
            "evicting_models": evicting_models,
        },
    )


@event_factory
def OomRecoverySucceeded(
    request_id: str,
    model_id: str,
    gateway_id: str,
    evicted_count: int,
) -> Event:
    """Emit when retry after OOM recovery succeeds."""
    return Event(
        signal=ROUTING_INFERENCE_OOM_RECOVERY_SUCCEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "evicted_count": evicted_count,
        },
    )


