"""Request failure, timeout, deadline, and disconnect event signals.

Separated from lifecycle because the REQUEST_FAILED diagnostic docstring is
large enough to push a combined module over the SLOC ceiling.
Imported via the ``request`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

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

REQUEST_DEADLINE_EXCEEDED = "request.deadline.exceeded"
"""
Inference budget exceeded (X-Request-Timeout deadline reached mid-inference).

Distinct from `request.timed.out` (queue TTL expired before admission).

Payload: {
    "request_id": str,
    "model_id": str,
    "gateway_id": str,
    "deadline_s": float,
    "elapsed_ms": int
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
def RequestDeadlineExceeded(
    request_id: str,
    model_id: str,
    gateway_id: str,
    deadline_s: float,
    elapsed_ms: int,
) -> Event:
    """
    X-Request-Timeout deadline reached during inference.

    Distinct from queue TTL expiry (request.timed.out).
    """
    return Event(
        signal=REQUEST_DEADLINE_EXCEEDED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "deadline_s": deadline_s,
            "elapsed_ms": elapsed_ms,
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
