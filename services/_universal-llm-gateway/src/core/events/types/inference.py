"""Inference and stream lifecycle event signals and factories.

Covers request queueing, model-scoped busy/idle transitions, inference
failure, and stream cancellation. Called from chat completion routers and
resource tracker busy/idle transitions.
"""

# ruff: noqa: N802 - Factory function names match event signal names

from typing import Any

from universal_event_bus import Event, event_factory

# ========== Inference Lifecycle Event Signals ==========

REQUEST_QUEUED = "request.queued"
"""
Emitted when a request is queued for processing (immediately before
semaphore acquisition).

Payload:
    model_id: str - Unique identifier for the model that will process the request
    request_id: str - Unique identifier for this request
    messages: List[Dict[str, str]] - The chat messages or prompt being processed
    parameters: Dict[str, Any] - Generation parameters (temperature, max_tokens, etc.)
    stream: bool - Whether this is a streaming request
"""

REQUEST_INFERENCE_STARTED = "request.inference.started"
"""
Emitted when gateway runtime execution begins for a specific request.

Payload:
    request_id: str - Unique identifier for this request
    model_id: str - Unique identifier for the model handling the request
    gateway_url: str - Gateway URL/identity where runtime execution starts
    correlation_id: Optional[str] - Cross-service trace correlation identifier
"""

INFERENCE_STARTED = "inference.started"
"""
Emitted when a model transitions to BUSY state (inference begins).

**Contract**: Model-scoped lifecycle event (not request-scoped).

Payload:
    model_id: str - Unique identifier for the model performing inference

**Invariant**: ∀ inference_start, ∃! emission via resource_tracker.set_model_busy()

Note: For request-level tracking, use REQUEST_QUEUED event instead.
"""

INFERENCE_COMPLETED = "inference.completed"
"""
Emitted when a model transitions from BUSY to LOADED state (inference ends).

**Contract**: Model-scoped lifecycle event (not request-scoped).

Payload:
    model_id: str - Unique identifier for the model that performed inference
    last_inference_time: float - Unix timestamp when inference completed
        (for LRU eviction)

**Invariant**: ∀ inference_end, ∃! emission via resource_tracker.set_model_idle()

Note: For request-level completion tracking with duration/tokens, use other events.
"""

INFERENCE_FAILED = "inference.failed"
"""
Emitted when an inference request fails.

Payload:
    model_id: str - Unique identifier for the model that attempted inference
    request_id: str - Unique identifier for this inference request
    error_message: str - Description of the error that occurred
"""

STREAM_CANCELLED = "stream.cancelled"
"""
Emitted when a streaming inference request is cancelled.

Payload:
    model_id: str - Unique identifier for the model performing inference
    stream_id: str - Stream ID of the cancelled stream
    reason: str - Reason for cancellation
        (e.g., "client_disconnect", "explicit_cancellation")
    worker_ready: bool - Whether the worker is immediately ready for new requests
"""

STREAM_CANCELLATION_COMPLETE = "stream.cancellation.complete"
"""
Emitted when stream cancellation cleanup is complete.

Payload:
    model_id: str - Unique identifier for the model
    stream_id: str - Stream ID of the cancelled stream
    cleanup_duration: float - Time taken for cleanup in seconds
"""


# Inference Lifecycle Event Factories
@event_factory
def RequestQueued(
    model_id: str,
    request_id: str,
    messages: list[dict[str, str]],
    parameters: dict[str, Any],
    stream: bool,
) -> Event:
    """
    Create REQUEST_QUEUED event.

    Args:
        model_id: Model that will process the request
        request_id: Unique identifier for this request
        messages: Chat messages or prompt
        parameters: Generation parameters (temperature, max_tokens, etc.)
        stream: Whether this is a streaming request

    Returns:
        Event with RequestQueued signal
    """
    return Event(
        signal=REQUEST_QUEUED,
        payload={
            "model_id": model_id,
            "request_id": request_id,
            "messages": messages,
            "parameters": parameters,
            "stream": stream,
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

    Request-scoped runtime boundary event emitted at execution handoff.

    Args:
        request_id: Unique identifier for this request
        model_id: Model handling the request
        gateway_url: Gateway URL/identity for runtime start
        correlation_id: Optional cross-service trace correlation ID

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
def InferenceStarted(model_id: str) -> Event:
    """
    Create INFERENCE_STARTED event.

    Model-scoped lifecycle event (not request-scoped).

    Args:
        model_id: Model performing inference

    Returns:
        Event with InferenceStarted signal
    """
    return Event(signal=INFERENCE_STARTED, payload={"model_id": model_id})


@event_factory
def InferenceCompleted(model_id: str, last_inference_time: float) -> Event:
    """
    Create INFERENCE_COMPLETED event.

    Model-scoped lifecycle event (not request-scoped).

    Args:
        model_id: Model that performed inference
        last_inference_time: Unix timestamp when inference completed (for LRU)

    Returns:
        Event with InferenceCompleted signal
    """
    return Event(
        signal=INFERENCE_COMPLETED,
        payload={"model_id": model_id, "last_inference_time": last_inference_time},
    )


@event_factory
def InferenceFailed(model_id: str, request_id: str, error_message: str) -> Event:
    """
    Create INFERENCE_FAILED event.

    Args:
        model_id: Model that attempted inference
        request_id: Request that failed
        error_message: Description of the failure

    Returns:
        Event with InferenceFailed signal
    """
    return Event(
        signal=INFERENCE_FAILED,
        payload={
            "model_id": model_id,
            "request_id": request_id,
            "error_message": error_message,
        },
    )


@event_factory
def StreamCancelled(
    model_id: str, stream_id: str, reason: str, worker_ready: bool
) -> Event:
    """
    Create STREAM_CANCELLED event.

    Args:
        model_id: Model performing inference
        stream_id: Stream ID of cancelled stream
        reason: Reason for cancellation (e.g., "client_disconnect")
        worker_ready: Whether worker is immediately ready for new requests

    Returns:
        Event with StreamCancelled signal
    """
    return Event(
        signal=STREAM_CANCELLED,
        payload={
            "model_id": model_id,
            "stream_id": stream_id,
            "reason": reason,
            "worker_ready": worker_ready,
        },
    )


@event_factory
def StreamCancellationComplete(
    model_id: str, stream_id: str, cleanup_duration: float
) -> Event:
    """
    Create STREAM_CANCELLATION_COMPLETE event.

    Args:
        model_id: Model identifier
        stream_id: Stream ID of the cancelled stream
        cleanup_duration: Time taken for cleanup in seconds

    Returns:
        Event with StreamCancellationComplete signal
    """
    return Event(
        signal=STREAM_CANCELLATION_COMPLETE,
        payload={
            "model_id": model_id,
            "stream_id": stream_id,
            "cleanup_duration": cleanup_duration,
        },
    )
