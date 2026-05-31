"""Stargate scheduling routing events — split module"
"(routing_factories_metrics_load.py)."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_metrics import (
    MODEL_LOAD_COMPLETED,
    MODEL_LOAD_INITIATED,
    REQUEST_GATEWAY_TRACE,
    REQUEST_ROUTED,
)

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
def RequestGatewayTrace(
    *,
    request_id: str,
    model_id: str,
    phase: str,
    selected_gateway: str | None = None,
    capacity_gateway: str | None = None,
    sticky_gateway: str | None = None,
    final_gateway: str | None = None,
    forwarded_gateway: str | None = None,
    remote_id: str | None = None,
    gateway_url: str | None = None,
    invariant_status: str = "incomplete",
    reason: str | None = None,
) -> Event:
    """Create REQUEST_GATEWAY_TRACE event."""
    return Event(
        signal=REQUEST_GATEWAY_TRACE,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "phase": phase,
            "selected_gateway": selected_gateway,
            "capacity_gateway": capacity_gateway,
            "sticky_gateway": sticky_gateway,
            "final_gateway": final_gateway,
            "forwarded_gateway": forwarded_gateway,
            "remote_id": remote_id,
            "gateway_url": gateway_url,
            "invariant_status": invariant_status,
            "reason": reason,
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
