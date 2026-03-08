# ruff: noqa: N802
"""Request lifecycle event signals.

Covers the full request lifecycle from queue entry to completion/failure,
plus the federation snapshot emitted by Edge Stargate.

Signals:
    request.queued — request added to queue
    request.processing — request started processing on gateway
    request.inference.started — request began downstream runtime execution
    request.profile.resolved — profile auto-assigned for request
    request.completed — request completed successfully
    request.failed — request failed
    request.timed.out — request timed out
    request.capacity.timeout — all capacity retries exhausted
    request.removed — request removed from queue (client disconnect)
    federation.snapshot.sent — Edge Stargate broadcast snapshot to Master
"""

from universal_event_bus import Event, event_factory

# ========================================
# Request Event Signals
# ========================================

REQUEST_QUEUED = "request.queued"
"""
Request added to queue
Payload: {
    "request_id": str,
    "model_id": str,
    "priority": int
}
"""

REQUEST_PROCESSING = "request.processing"
"""
Request started processing
Payload: {
    "request_id": str,
    "gateway_url": str,
    "model_id": str
}
"""

REQUEST_INFERENCE_STARTED = "request.inference.started"
"""
Request began inference at downstream runtime boundary.
Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_url": str,
    "correlation_id": Optional[str]
}
"""

REQUEST_PROFILE_RESOLVED = "request.profile.resolved"
"""
Request profile resolved during preparation.

Emitted after request preparation when a profile is in effect for the request
(auto-assigned by model basename or explicitly requested by client).

Payload: {
    "request_id": str,
    "model_id": str,
    "profile_name": str
}
"""

REQUEST_COMPLETED = "request.completed"
"""
Request completed successfully
Payload: {
    "request_id": str,
    "gateway_url": str,
    "model_id": str,
    "duration": float
}
"""

REQUEST_FAILED = "request.failed"
"""
Request failed
Payload: {
    "request_id": str,
    "gateway_url": Optional[str],
    "model_id": str,
    "error": str
}
"""

REQUEST_TIMEOUT = "request.timed.out"
"""
Request timed out
Payload: {
    "request_id": str,
    "gateway_url": Optional[str],
    "model_id": str,
    "timeout_seconds": float
}
"""

REQUEST_CAPACITY_TIMEOUT = "request.capacity.timeout"
"""
Capacity timeout — all retries exhausted waiting for model capacity.
Emitted before request.failed for immediate filtering.
Payload: {
    "request_id": str,
    "model_id": str,
    "timeout_seconds": float,
    "retry_count": int,
    "elapsed_s": float,
    "pipeline_step_id": Optional[str]
}
"""

REQUEST_REMOVED = "request.removed"
"""
Request removed from queue (e.g., client disconnect)
Payload: {
    "request_id": str,
    "reason": str,
    "model_id": str,
    "age_seconds": float
}
"""

FEDERATION_SNAPSHOT_SENT = "federation.snapshot.sent"
"""
Edge Stargate sent GATEWAY_SNAPSHOT to Master.

Payload documents all_models vs available_models gap — the difference
between what /v1/models shows and what Master can actually route.

Diagnostic query:
    jq 'select(.signal == "federation.snapshot.sent" and .payload.gap_count > 0)'

Payload: {
    "gateway_id": str,
    "all_models_count": int,     # from ws_client.get_models()
    "available_models_count": int, # models WITH resource data (routable)
    "gap_count": int,            # all_models_count - available_models_count
}
"""

MODEL_SELECTION_HEALTH_OBSERVATION = "model.selection.health.observation"
"""
Runtime health observation ingested for task-scoped model reputation.
Payload: {
    "task": str,
    "model_id": str,
    "outcome": str,
    "latency_ms": float,
    "quality_score": Optional[float],
    "tokens_per_second": Optional[float]
}
"""

MODEL_SELECTION_SCORE_UPDATED = "model.selection.score.updated"
"""
Per-candidate reputation score computed during model selection.
Payload: {
    "task": str,
    "model_id": str,
    "final_score": float,
    "components": dict[str, float]
}
"""

MODEL_SELECTION_RANK_COMPUTED = "model.selection.rank.computed"
"""
Final ranked candidate list produced by reputation-enabled selection.
Payload: {
    "task": str,
    "candidates": list[dict[str, object]],
    "selection_path": str
}
"""

MODEL_SELECTION_SWITCH_SUPPRESSED = "model.selection.switch.suppressed"
"""
Sticky anti-thrash suppressed a marginal top-rank switch.
Payload: {
    "task": str,
    "sticky_key": str,
    "current_model_id": str,
    "contender_model_id": str,
    "delta": float,
    "reason": str
}
"""

MODEL_SELECTION_SWITCH_ALLOWED = "model.selection.switch.allowed"
"""
Anti-thrash evaluated a switch and allowed it (delta >= min_switch_delta).
Payload: {
    "task": str,
    "sticky_key": str,
    "previous_model_id": str,
    "new_model_id": str,
    "delta": float
}
"""


# ========================================
# Factory Functions
# ========================================


@event_factory
def RequestQueued(
    request_id: str,
    model_id: str,
    priority: int,
) -> Event:
    """
    Create REQUEST_QUEUED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        priority: Request priority

    Returns:
        Event with RequestQueued signal
    """
    return Event(
        signal=REQUEST_QUEUED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "priority": priority,
        },
    )


@event_factory
def RequestProcessing(
    request_id: str,
    gateway_url: str,
    model_id: str,
) -> Event:
    """
    Create REQUEST_PROCESSING event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        gateway_url: Gateway processing request
        model_id: Model being used

    Returns:
        Event with RequestProcessing signal
    """
    return Event(
        signal=REQUEST_PROCESSING,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
        },
    )


@event_factory
def RequestProfileResolved(
    request_id: str,
    model_id: str,
    profile_name: str,
) -> Event:
    """
    Create REQUEST_PROFILE_RESOLVED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model selected for execution
        profile_name: Profile resolved for this request

    Returns:
        Event with RequestProfileResolved signal
    """
    return Event(
        signal=REQUEST_PROFILE_RESOLVED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "profile_name": profile_name,
        },
    )


@event_factory
def RequestInferenceStarted(
    request_id: str,
    model_id: str,
    gateway_url: str,
    correlation_id: str | None = None,
) -> Event:
    """
    Create REQUEST_INFERENCE_STARTED event.

    Emitted when Stargate receives downstream-confirmed runtime start telemetry.
    This boundary is later than request admission (request.processing).

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model selected for execution
        gateway_url: Gateway runtime endpoint reporting start
        correlation_id: Federated request chain correlation (optional)

    Returns:
        Event with RequestInferenceStarted signal
    """
    return Event(
        signal=REQUEST_INFERENCE_STARTED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_url": gateway_url,
            "correlation_id": correlation_id,
        },
    )


@event_factory
def RequestCompleted(
    request_id: str,
    gateway_url: str,
    model_id: str,
    duration: float,
) -> Event:
    """
    Create REQUEST_COMPLETED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        gateway_url: Gateway that processed request
        model_id: Model used
        duration: Request duration in seconds

    Returns:
        Event with RequestCompleted signal
    """
    return Event(
        signal=REQUEST_COMPLETED,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "duration": duration,
        },
    )


@event_factory
def RequestFailed(
    request_id: str,
    model_id: str,
    error: str,
    gateway_url: str | None = None,
) -> Event:
    """
    Create REQUEST_FAILED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        error: Error message
        gateway_url: Gateway URL (optional)

    Returns:
        Event with RequestFailed signal
    """
    return Event(
        signal=REQUEST_FAILED,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "error": error,
        },
    )


@event_factory
def RequestTimeout(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    gateway_url: str | None = None,
) -> Event:
    """
    Create REQUEST_TIMEOUT event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        timeout_seconds: Timeout value
        gateway_url: Gateway URL (optional)

    Returns:
        Event with RequestTimeout signal
    """
    return Event(
        signal=REQUEST_TIMEOUT,
        payload={
            "request_id": request_id,
            "gateway_url": gateway_url,
            "model_id": model_id,
            "timeout_seconds": timeout_seconds,
        },
    )


@event_factory
def RequestCapacityTimeout(
    request_id: str,
    model_id: str,
    timeout_seconds: float,
    retry_count: int,
    elapsed_s: float,
    pipeline_step_id: str | None = None,
) -> Event:
    """
    Create REQUEST_CAPACITY_TIMEOUT event.

    Emitted when all capacity retries are exhausted for a model.
    Distinct from request.failed — enables direct jq filtering:
        jq 'select(.signal == "request.capacity.timeout")'

    Args:
        request_id: Proxy request ID
        model_id: Model that had no capacity
        timeout_seconds: Total retry budget (seconds)
        retry_count: Number of retries attempted
        elapsed_s: Actual wall time spent retrying
        pipeline_step_id: Pipeline step (if request originated from pipeline)

    Returns:
        Event with RequestCapacityTimeout signal
    """
    payload: dict[str, object] = {
        "request_id": request_id,
        "model_id": model_id,
        "timeout_seconds": timeout_seconds,
        "retry_count": retry_count,
        "elapsed_s": elapsed_s,
    }
    if pipeline_step_id:
        payload["pipeline_step_id"] = pipeline_step_id
    return Event(
        signal=REQUEST_CAPACITY_TIMEOUT,
        payload=payload,
    )


@event_factory
def RequestRemoved(
    request_id: str, reason: str, model_id: str, age_seconds: float
) -> Event:
    """
    Create REQUEST_REMOVED event.

    Args:
        request_id: Request identifier
        reason: Removal reason
        model_id: Model requested
        age_seconds: How long request was queued

    Returns:
        Event with RequestRemoved signal
    """
    return Event(
        signal=REQUEST_REMOVED,
        payload={
            "request_id": request_id,
            "reason": reason,
            "model_id": model_id,
            "age_seconds": age_seconds,
        },
    )


@event_factory
def FederationSnapshotSent(
    gateway_id: str,
    all_models_count: int,
    available_models_count: int,
) -> Event:
    """
    Create FEDERATION_SNAPSHOT_SENT event.

    Emitted by Edge Stargate when it broadcasts GATEWAY_SNAPSHOT to Master.
    Documents the gap between all models (visible in /v1/models) and
    routable models (those with resource data in model_details).

    A non-zero gap_count means some models will route as MODEL_NOT_FOUND
    despite appearing in /v1/models — see gateway.snapshot.resource.gap
    in the Edge Gateway events for root cause.

    Args:
        gateway_id: Gateway identifier
        all_models_count: Total models from ws_client.get_models()
        available_models_count: Models with resource data (routable by Master)

    Returns:
        Event with FederationSnapshotSent signal
    """
    return Event(
        signal=FEDERATION_SNAPSHOT_SENT,
        payload={
            "gateway_id": gateway_id,
            "all_models_count": all_models_count,
            "available_models_count": available_models_count,
            "gap_count": all_models_count - available_models_count,
        },
    )


@event_factory
def ModelSelectionHealthObservation(
    *,
    task: str,
    model_id: str,
    outcome: str,
    latency_ms: float,
    quality_score: float | None = None,
    tokens_per_second: float | None = None,
) -> Event:
    """Create MODEL_SELECTION_HEALTH_OBSERVATION event."""
    payload: dict[str, object] = {
        "task": task,
        "model_id": model_id,
        "outcome": outcome,
        "latency_ms": latency_ms,
    }
    if quality_score is not None:
        payload["quality_score"] = quality_score
    if tokens_per_second is not None:
        payload["tokens_per_second"] = tokens_per_second
    return Event(signal=MODEL_SELECTION_HEALTH_OBSERVATION, payload=payload)


@event_factory
def ModelSelectionScoreUpdated(
    *,
    task: str,
    model_id: str,
    final_score: float,
    components: dict[str, float],
) -> Event:
    """Create MODEL_SELECTION_SCORE_UPDATED event."""
    return Event(
        signal=MODEL_SELECTION_SCORE_UPDATED,
        payload={
            "task": task,
            "model_id": model_id,
            "final_score": final_score,
            "components": components,
        },
    )


@event_factory
def ModelSelectionRankComputed(
    *,
    task: str,
    candidates: list[dict[str, object]],
    selection_path: str,
) -> Event:
    """Create MODEL_SELECTION_RANK_COMPUTED event."""
    return Event(
        signal=MODEL_SELECTION_RANK_COMPUTED,
        payload={
            "task": task,
            "candidates": candidates,
            "selection_path": selection_path,
        },
    )


@event_factory
def ModelSelectionSwitchSuppressed(
    *,
    task: str,
    sticky_key: str,
    current_model_id: str,
    contender_model_id: str,
    delta: float,
    reason: str,
) -> Event:
    """Create MODEL_SELECTION_SWITCH_SUPPRESSED event."""
    return Event(
        signal=MODEL_SELECTION_SWITCH_SUPPRESSED,
        payload={
            "task": task,
            "sticky_key": sticky_key,
            "current_model_id": current_model_id,
            "contender_model_id": contender_model_id,
            "delta": delta,
            "reason": reason,
        },
    )


@event_factory
def ModelSelectionSwitchAllowed(
    *,
    task: str,
    sticky_key: str,
    previous_model_id: str,
    new_model_id: str,
    delta: float,
) -> Event:
    """Create MODEL_SELECTION_SWITCH_ALLOWED event."""
    return Event(
        signal=MODEL_SELECTION_SWITCH_ALLOWED,
        payload={
            "task": task,
            "sticky_key": sticky_key,
            "previous_model_id": previous_model_id,
            "new_model_id": new_model_id,
            "delta": delta,
        },
    )
