"""Request admission and successful-completion event signals.

Covers queue entry through processing/inference/profile/alias resolution and
successful completion. Failure/timeout/disconnect signals live in ``failure``.
Imported via the ``request`` package facade."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

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
