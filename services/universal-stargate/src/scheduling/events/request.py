# ruff: noqa: N802
"""Request lifecycle event signals.

Covers the full request lifecycle from queue entry to completion/failure,
plus the federation snapshot emitted by Edge Stargate.

Signals:
    request.queued — request added to queue
    request.processing — request started processing on gateway
    request.inference.started — request began downstream runtime execution
    request.profile.resolved — profile auto-assigned for request
    request.alias.resolved — persona alias resolved to backing model
    request.completed — request completed successfully
    request.failed — request failed
    request.timed.out — request timed out
    request.client.disconnected — downstream/client stream disconnected
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

REQUEST_ALIAS_RESOLVED = "request.alias.resolved"
"""
Persona alias resolved at ingress to a backing model.

Payload: {
    "request_id": str,
    "alias_id": str,
    "backing_model_id": str
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
    "error": str,                  # raw exception string (legacy)
    "error_code": Optional[str],   # envelope code, e.g. "MODEL_NOT_FOUND"
    "error_source": Optional[str], # envelope source ("master", "edge", provider)
    "error_data": Optional[dict],  # envelope data dict (model_id, etc.)
                                   # MODEL_NOT_FOUND: includes topology_snapshot
                                   # {connected_edge_count, total_edges_known,
                                   #  configured_remote_count,
                                   #  not_seen_remote_count, not_seen_remotes[],
                                   #  unreachable_edges[{gateway_id, remote_id,
                                   #    last_heartbeat_age_ms,
                                   #    cached_catalog_match}],
                                   #  cached_only_edges[{gateway_id,
                                   #    cached_catalog_match}],
                                   #  connected_cloud_gateway_count,
                                   #  connected_cloud_gateways[]}
                                   # Edges (federated remotes) and cloud
                                   # gateways are reported separately so the
                                   # edge denominator reflects configured
                                   # remotes, not provider count.
                                   # not_seen_remotes is the configured-but-
                                   # never-connected set, sourced from
                                   # FederationConfig.remotes
    "caller_hint": Optional[dict]  # {user_agent, x_caller,
                                   #  pipeline_execution_id, pipeline_step_id}
}

Diagnostic queries:
    -- All MODEL_NOT_FOUND grouped by caller
    SELECT json_extract(payload,'$.model_id') as model,
           json_extract(payload,'$.caller_hint.user_agent') as ua,
           json_extract(payload,'$.caller_hint.x_caller') as caller,
           COUNT(*) FROM events
    WHERE signal='request.failed'
      AND json_extract(payload,'$.error_code')='MODEL_NOT_FOUND'
    GROUP BY model, ua, caller;

    -- Pipeline-unavailable failures (model_id is a registered pipeline
    -- whose model deps could not be resolved)
    SELECT json_extract(payload,'$.model_id') as pipeline,
           json_extract(payload,'$.error_data.unavailable_pipeline.missing_models')
             as missing
    FROM events
    WHERE signal='request.failed'
      AND json_extract(payload,'$.error_data.unavailable_pipeline') IS NOT NULL;

    -- MODEL_NOT_FOUND failures whose model is cached on an offline edge
    -- (the topology-self-explanatory case: "target edge is down")
    SELECT json_extract(payload,'$.model_id') as model,
           json_extract(payload,'$.error_data.topology_snapshot.connected_edge_count')
             as connected,
           json_extract(payload,'$.error_data.topology_snapshot.unreachable_edges')
             as unreachable
    FROM events
    WHERE signal='request.failed'
      AND json_extract(payload,'$.error_code')='MODEL_NOT_FOUND'
      AND json_extract(payload,'$.error_data.topology_snapshot') IS NOT NULL;
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

REQUEST_CLIENT_DISCONNECTED = "request.client.disconnected"
"""
Request stream terminated because the downstream client disconnected.
Payload: {
    "request_id": str,
    "model_id": str,
    "hop": str,
    "gateway_url": Optional[str],
    "duration": Optional[float]
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
def RequestAliasResolved(
    request_id: str,
    alias_id: str,
    backing_model_id: str,
) -> Event:
    """Create REQUEST_ALIAS_RESOLVED event."""
    return Event(
        signal=REQUEST_ALIAS_RESOLVED,
        payload={
            "request_id": request_id,
            "alias_id": alias_id,
            "backing_model_id": backing_model_id,
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
    error_code: str | None = None,
    error_source: str | None = None,
    error_data: dict[str, object] | None = None,
    caller_hint: dict[str, object] | None = None,
) -> Event:
    """
    Create REQUEST_FAILED event.

    INVARIANT: request_id always present (proxy request ID for tracking)

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        error: Raw exception string (legacy compatibility)
        gateway_url: Gateway URL (optional)
        error_code: Structured envelope code (e.g. "MODEL_NOT_FOUND") for SQL filtering
        error_source: Envelope source ("master", "edge", upstream provider)
        error_data: Envelope data dict — may include "unavailable_pipeline":
            {pipeline_id, missing_models} when the requested ID is a registered
            pipeline whose model deps could not be resolved
        caller_hint: Best-effort caller identification:
            {user_agent, x_caller, pipeline_execution_id, pipeline_step_id}

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
            "error_code": error_code,
            "error_source": error_source,
            "error_data": error_data,
            "caller_hint": caller_hint,
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
def RequestClientDisconnected(
    request_id: str,
    model_id: str,
    hop: str,
    gateway_url: str | None = None,
    duration: float | None = None,
) -> Event:
    """
    Create REQUEST_CLIENT_DISCONNECTED event.

    Args:
        request_id: Proxy request ID for tracking and tracing
        model_id: Model requested
        hop: Component that observed the disconnect
        gateway_url: Gateway URL (optional)
        duration: Seconds elapsed before disconnect (optional)

    Returns:
        Event with RequestClientDisconnected signal
    """
    payload: dict[str, object] = {
        "request_id": request_id,
        "model_id": model_id,
        "hop": hop,
    }
    if gateway_url is not None:
        payload["gateway_url"] = gateway_url
    if duration is not None:
        payload["duration"] = duration
    return Event(signal=REQUEST_CLIENT_DISCONNECTED, payload=payload)


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
    trigger: str = "initial",
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
        trigger: What caused this snapshot ("initial" at wiring, "periodic"
                 from reconciliation timer)

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
            "trigger": trigger,
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
