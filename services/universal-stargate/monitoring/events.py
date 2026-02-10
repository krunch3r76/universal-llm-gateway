"""
Monitoring event factory functions.

Creates structured events for monitoring system operations.
All monitoring events use the Event structure with consistent payloads.
"""
# ruff: noqa: N802  # Factory functions use PascalCase

from typing import Any

from universal_event_bus import Event, event_factory

# Constants
MONITORING_CHAT_COMPLETION = "monitoring.chat.completed"
MONITORING_STREAMING_CHUNK = "monitoring.stream.chunk"
MONITORING_PARAMETER_COMPARISON = "monitoring.parameters.compared"
MONITORING_ERROR = "monitoring.error.occurred"
MONITORING_REQUEST_INFO = "monitoring.request.info"
MONITORING_PRE_PROCESSING = "monitoring.preprocessing.completed"


@event_factory
def MonitoringChatCompletion(
    event_id: str,
    timestamp: str,
    request_id: str,
    original_request: dict[str, Any],
    modified_request: dict[str, Any],
    stargate_actions: list[str],
    processing_time_ms: float,
    gateway_endpoint: str,
    response: dict[str, Any] | None = None,
    token_metrics: dict[str, Any] | None = None,
    model_metadata: dict[str, Any] | None = None,
) -> Event:
    """
    Create MONITORING_CHAT_COMPLETION event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        request_id: Request identifier
        original_request: Original request payload
        modified_request: Modified request payload
        stargate_actions: List of stargate middleware actions
        processing_time_ms: Processing time in milliseconds
        gateway_endpoint: Gateway endpoint URL
        response: Response data (optional)
        token_metrics: Token usage metrics (optional)
        model_metadata: Model metadata (optional)

    Returns:
        Event with MONITORING_CHAT_COMPLETION signal
    """
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "type": "chat_completion",
        "request_id": request_id,
        "original_request": original_request,
        "modified_request": modified_request,
        "stargate_actions": stargate_actions,
        "processing_time_ms": processing_time_ms,
        "gateway_endpoint": gateway_endpoint,
    }
    if response is not None:
        payload["response"] = response
    if token_metrics is not None:
        payload["token_metrics"] = token_metrics
    if model_metadata is not None:
        payload["model_metadata"] = model_metadata

    return Event(signal=MONITORING_CHAT_COMPLETION, payload=payload)


@event_factory
def MonitoringStreamingChunk(
    event_id: str,
    timestamp: str,
    event_type: str,
    request_id: str,
    chunk_number: int | None = None,
    chunk: str | None = None,
    start_chunk_number: int | None = None,
    chunk_count: int | None = None,
    content: str | None = None,
    token_metrics: dict[str, Any] | None = None,
) -> Event:
    """
    Create MONITORING_STREAMING_CHUNK event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        event_type: Event subtype ('streaming_chunk' or 'streaming_chunk_batch')
        request_id: Request identifier
        chunk_number: Chunk number (for single chunk)
        chunk: Chunk content (for single chunk)
        start_chunk_number: Starting chunk number (for batch)
        chunk_count: Number of chunks (for batch)
        content: Combined content (for batch)
        token_metrics: Token usage metrics (optional)

    Returns:
        Event with MONITORING_STREAMING_CHUNK signal
    """
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "type": event_type,
        "request_id": request_id,
    }
    if chunk_number is not None:
        payload["chunk_number"] = chunk_number
    if chunk is not None:
        payload["chunk"] = chunk
    if start_chunk_number is not None:
        payload["start_chunk_number"] = start_chunk_number
    if chunk_count is not None:
        payload["chunk_count"] = chunk_count
    if content is not None:
        payload["content"] = content
    if token_metrics is not None:
        payload["token_metrics"] = token_metrics

    return Event(signal=MONITORING_STREAMING_CHUNK, payload=payload)


@event_factory
def MonitoringParameterComparison(
    event_id: str,
    timestamp: str,
    model_id: str,
    user_parameters: dict[str, Any],
    model_defaults: dict[str, Any],
    final_parameters: dict[str, Any],
    parameter_changes: list[dict[str, Any]],
    processing_time_ms: float,
    gateway_endpoint: str,
    summary: dict[str, Any],
) -> Event:
    """
    Create MONITORING_PARAMETER_COMPARISON event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        model_id: Model identifier
        user_parameters: User-provided parameters
        model_defaults: Model default parameters
        final_parameters: Final merged parameters
        parameter_changes: List of parameter changes
        processing_time_ms: Processing time in milliseconds
        gateway_endpoint: Gateway endpoint URL
        summary: Parameter comparison summary

    Returns:
        Event with MONITORING_PARAMETER_COMPARISON signal
    """
    return Event(
        signal=MONITORING_PARAMETER_COMPARISON,
        payload={
            "id": event_id,
            "timestamp": timestamp,
            "type": "parameter_comparison",
            "model_id": model_id,
            "user_parameters": user_parameters,
            "model_defaults": model_defaults,
            "final_parameters": final_parameters,
            "parameter_changes": parameter_changes,
            "processing_time_ms": processing_time_ms,
            "gateway_endpoint": gateway_endpoint,
            "summary": summary,
        },
    )


@event_factory
def MonitoringError(
    event_id: str,
    timestamp: str,
    error_message: str,
    original_request: dict[str, Any],
    processing_time_ms: float,
    stargate_actions: list[str],
    token_metrics: dict[str, Any] | None = None,
    gateway_error_details: dict[str, Any] | None = None,
) -> Event:
    """
    Create MONITORING_ERROR event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        error_message: Error message
        original_request: Original request payload
        processing_time_ms: Processing time in milliseconds
        stargate_actions: List of stargate actions taken
        token_metrics: Token usage metrics (optional)
        gateway_error_details: Gateway error details (optional)

    Returns:
        Event with MONITORING_ERROR signal
    """
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "type": "stargate_error",
        "error_message": error_message,
        "original_request": original_request,
        "processing_time_ms": processing_time_ms,
        "stargate_actions": stargate_actions,
    }
    if token_metrics is not None:
        payload["token_metrics"] = token_metrics
    if gateway_error_details is not None:
        payload["gateway_error_details"] = gateway_error_details

    return Event(signal=MONITORING_ERROR, payload=payload)


@event_factory
def MonitoringRequestInfo(
    event_id: str,
    timestamp: str,
    request_id: str,
    original_request: dict[str, Any],
    selected_model: str,
    profile_name: str | None = None,
) -> Event:
    """
    Create MONITORING_REQUEST_INFO event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        request_id: Request identifier
        original_request: Original request payload
        selected_model: Selected model identifier
        profile_name: Profile name (optional)

    Returns:
        Event with MONITORING_REQUEST_INFO signal
    """
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "type": "request_info",
        "request_id": request_id,
        "original_request": original_request,
        "modified_request": None,  # Not available yet
        "selected_model": selected_model,
        "response": None,  # No response yet
    }
    if profile_name is not None:
        payload["profile_name"] = profile_name

    return Event(signal=MONITORING_REQUEST_INFO, payload=payload)


@event_factory
def MonitoringPreProcessing(
    event_id: str,
    timestamp: str,
    event_subtype: str,
    request_id: str,
    original_request: dict[str, Any],
    modified_request: dict[str, Any],
    stargate_actions: list[str],
    processing_time_ms: float,
    gateway_endpoint: str,
    token_metrics: dict[str, Any] | None = None,
    model_metadata: dict[str, Any] | None = None,
) -> Event:
    """
    Create MONITORING_PRE_PROCESSING event.

    Args:
        event_id: Unique event identifier
        timestamp: ISO 8601 timestamp
        event_subtype: Event subtype (e.g., 'pre_processing',
            'pre_processing_with_tokens')
        request_id: Request identifier
        original_request: Original request payload
        modified_request: Modified request payload
        stargate_actions: List of stargate middleware actions
        processing_time_ms: Processing time in milliseconds
        gateway_endpoint: Gateway endpoint URL
        token_metrics: Token usage metrics (optional)
        model_metadata: Model metadata (optional)

    Returns:
        Event with MONITORING_PRE_PROCESSING signal
    """
    payload = {
        "id": event_id,
        "timestamp": timestamp,
        "type": "pre_processing",
        "event_subtype": event_subtype,
        "request_id": request_id,
        "original_request": original_request,
        "modified_request": modified_request,
        "stargate_actions": stargate_actions,
        "processing_time_ms": processing_time_ms,
        "gateway_endpoint": gateway_endpoint,
        "response": None,  # No response yet
    }
    if token_metrics is not None:
        payload["token_metrics"] = token_metrics
    if model_metadata is not None:
        payload["model_metadata"] = model_metadata

    return Event(signal=MONITORING_PRE_PROCESSING, payload=payload)
