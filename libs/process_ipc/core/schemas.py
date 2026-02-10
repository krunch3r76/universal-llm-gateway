"""
Schema definitions for process_ipc message structures.

Defines the complete schema hierarchy for all message types used in process_ipc,
from the top-level IPC envelope down to domain-specific command responses.
"""

from typing import Any, TypedDict, Union

from . import signals


class IPCMessage(TypedDict):
    """
    Top-level IPC message envelope.

    This is the outermost envelope that wraps all messages sent through
    the process_ipc transport layer.
    """

    signal: str  # Message type (e.g., "command", "command_complete")
    payload: dict[str, Any]  # Command payload envelope (see CommandPayload)
    id: str  # Unique message ID
    timestamp: str  # ISO 8601 timestamp
    correlation_id: str | None  # For request/response tracking


class CommandPayload(TypedDict):
    """
    Command payload envelope.

    This wraps domain-specific command data with metadata.
    Used for both requests and responses.
    """

    worker_id: str  # Target/source worker identifier
    command_type: str  # Command type (e.g., "ping", "inference")
    result: dict[str, Any] | None  # Domain response data (for responses)
    error: str | None  # Error message (for error responses)
    correlation_id: str | None  # Echo of request correlation_id


class PingRequest(TypedDict):
    """Ping command request payload."""

    timestamp: float  # Request timestamp


class PingResponse(TypedDict):
    """Ping command response payload."""

    status: str  # Always "pong"
    timestamp: str  # ISO 8601 response timestamp
    model_id: str  # Model identifier
    model_loaded: bool  # Whether model is loaded
    worker_id: str  # Worker identifier


class InferenceRequest(TypedDict):
    """Inference command request payload."""

    messages: list[dict[str, str]] | str  # Input messages or prompt
    parameters: dict[str, Any]  # Inference parameters


class InferenceResponse(TypedDict):
    """Inference command response payload."""

    choices: list[dict[str, Any]]  # OpenAI-format choices
    usage: dict[str, Any]  # Token usage information
    model: str  # Model identifier
    finish_reason: str | None  # Completion reason


class ErrorResponse(TypedDict):
    """Error response payload."""

    error: str  # Error message
    error_type: str | None  # Error classification
    suggestion: str | None  # Suggested resolution


# Union type for all possible domain responses
DomainResponse = Union[PingResponse, InferenceResponse, ErrorResponse]


def extract_domain_data(message: dict[str, Any]) -> dict[str, Any]:
    """
    Extract domain data from process_ipc message (Simple UML Message format).

    Extracts domain data from the simple UML Message format:
    - Signal/payload format: {"signal": "...", "correlation_id": "...", "payload": {...}}
    - For commands: Payload contains domain data directly (no result wrapper)
    - For streaming (e.g., DATA_STREAM, STREAM_CHUNK): Payload is {"data": {...}},
      and this function extracts the inner data.

    The distinction between message types is made by checking the `signal` field
    of the message, which avoids ambiguity in payload structure.

    Args:
        message: Message from process_ipc in Simple UML Message format, which
                 must contain "signal" and "payload" keys.

    Returns:
        Dict[str, Any]: Domain-specific response data

    Raises:
        ValueError: If message structure is invalid or missing payload

    Example:
        >>> # Simple UML Message format (command response)
        >>> message = {
        ...     "signal": "command_complete",
        ...     "correlation_id": "req_123",
        ...     "payload": {"success": True, "model_loaded": True}
        ... }
        >>> domain_data = extract_domain_data(message)
        >>> domain_data["model_loaded"]  # True

        >>> # Simple UML Message format (streaming with DATA_STREAM)
        >>> message = {
        ...     "signal": signals.DATA_STREAM,
        ...     "correlation_id": "req_123",
        ...     "payload": {"data": {"chunk": "..."}}
        ... }
        >>> domain_data = extract_domain_data(message)
        >>> domain_data["chunk"]  # "..."

        >>> # Ambiguous command response - handled correctly
        >>> message = {
        ...     "signal": signals.COMMAND_COMPLETE,
        ...     "correlation_id": "req_456",
        ...     "payload": {"data": "This is a legitimate single data key"}
        ... }
        >>> domain_data = extract_domain_data(message)
        >>> domain_data["data"] # "This is a legitimate single data key"
    """
    # Validate message structure
    if "payload" not in message or "signal" not in message:
        raise ValueError(
            f"Message missing 'payload' or 'signal' field. Expected Simple UML Message format. "
            f"Got keys: {list(message.keys())}"
        )

    if not isinstance(message["payload"], dict):
        raise ValueError(
            f"Message 'payload' must be a dictionary. "
            f"Got type: {type(message['payload']).__name__}"
        )

    payload = message["payload"]
    signal = message["signal"]

    # For streaming signals, the domain data is nested inside the "data" key of the payload.
    # This check is now unambiguous because it relies on the signal type.
    streaming_signals = {signals.DATA_STREAM, signals.STREAM_CHUNK}
    if signal in streaming_signals:
        if "data" in payload:
            return payload["data"]
        else:
            # A streaming signal should have a "data" key. If not, return the
            # payload as-is to avoid breaking, but this may indicate a sender-side issue.
            return payload

    # For all other signals (e.g., COMMAND_COMPLETE), the payload is the domain data.
    return payload


def create_command_payload(
    worker_id: str,
    command_type: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    correlation_id: str | None = None,
) -> CommandPayload:
    """
    Create a properly structured command payload envelope.

    Args:
        worker_id: Target/source worker identifier
        command_type: Command type
        result: Domain response data (for responses)
        error: Error message (for error responses)
        correlation_id: Request correlation ID

    Returns:
        CommandPayload: Structured command payload
    """
    payload: CommandPayload = {"worker_id": worker_id, "command_type": command_type}

    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id

    return payload


def validate_command_payload(payload: dict[str, Any]) -> bool:
    """
    Validate that a payload conforms to CommandPayload schema.

    Args:
        payload: Payload to validate

    Returns:
        bool: True if valid, False otherwise
    """
    required_fields = {"worker_id", "command_type"}
    if not all(field in payload for field in required_fields):
        return False

    # Must have either result or error, but not both
    has_result = "result" in payload
    has_error = "error" in payload

    if not (has_result or has_error):
        return False
    if has_result and has_error:
        return False

    return True
