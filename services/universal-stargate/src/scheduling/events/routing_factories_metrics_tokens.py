"""Stargate scheduling routing events — split module"
"(routing_factories_metrics_tokens.py)."""

# ruff: noqa: N802

from universal_event_bus import Event, event_factory

from .routing_signal_constants_metrics import (
    TOKEN_COUNT_COMPLETED,
    TOKEN_COUNT_PRECONDITION,
    TOKEN_COUNTING_FAILED,
)


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
def TokenCountPrecondition(
    *,
    request_id: str,
    model_id: str,
    target_gateway: str,
    selected_gateway: str | None,
    gateway_url: str | None,
    remote_id: str | None,
    sticky: bool,
    loaded_on_gateway: bool,
    known_to_gateway: bool,
    skip_requested: bool,
    legal_reason: str,
    content_type: str | None,
    tools_count: int,
) -> Event:
    """Create TOKEN_COUNT_PRECONDITION event."""
    return Event(
        signal=TOKEN_COUNT_PRECONDITION,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "target_gateway": target_gateway,
            "selected_gateway": selected_gateway,
            "gateway_url": gateway_url,
            "remote_id": remote_id,
            "sticky": sticky,
            "loaded_on_gateway": loaded_on_gateway,
            "known_to_gateway": known_to_gateway,
            "skip_requested": skip_requested,
            "legal_reason": legal_reason,
            "content_type": content_type,
            "tools_count": tools_count,
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
