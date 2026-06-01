"""
Message structure utilities for Universal Event Bus compatible IPC.

Provides message creation, validation, and parsing utilities that follow
the Event Bus schema: {signal, payload, id, timestamp, correlation_id}
"""

import uuid
from datetime import datetime
from typing import Any, TypedDict

from universal_logging import get_logger

from . import signals
from .exceptions import ProcessError


class MessageStructure(TypedDict, total=False):
    """
    Type definition for IPC message structure.

    All messages follow this structure to be compatible with the
    Universal Event Bus schema used in universal-llm-gateway.
    """

    signal: str  # Message type/signal name (required)
    payload: dict[str, Any]  # All domain-specific data (required)
    id: str  # Unique message ID (required)
    timestamp: str  # ISO 8601 timestamp (required)
    correlation_id: (
        str | None
    )  # For request/response tracking (optional but recommended)


class ValidationError(ProcessError):
    """Raised when message validation fails."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, process_id="validation")
        self.details = details or {}


def generate_message_id(worker_id: str | None = None) -> str:
    """
    Generate a unique message ID.

    Args:
        worker_id: Optional worker identifier to include in message ID

    Returns:
        str: Unique message ID
    """
    if worker_id:
        # Use worker-specific format for easier debugging
        return f"{worker_id}_{int(datetime.now().timestamp() * 1000000)}"
    else:
        # Use UUID for manager messages
        return f"msg_{uuid.uuid4().hex[:12]}"


def generate_correlation_id(prefix: str = "req") -> str:
    """
    Generate a unique correlation ID for request/response tracking.

    Args:
        prefix: Optional prefix for the correlation ID

    Returns:
        str: Unique correlation ID
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def create_message(
    signal: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    message_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """
    Create a properly structured IPC message.

    Args:
        signal: Signal/message type (e.g., "command", "command_complete")
        payload: All domain-specific data including worker_id, results, etc.
        correlation_id: Optional correlation ID for request/response tracking
        message_id: Optional custom message ID (generated if not provided)
        worker_id: Optional worker ID to use in message ID generation

    Returns:
        Dict[str, Any]: Properly structured message

    Example:
        >>> create_message(
        ...     signal="command_complete",
        ...     payload={"worker_id": "model-123", "result": {...}},
        ...     correlation_id="req_abc123"
        ... )
        {
            "signal": "command_complete",
            "payload": {"worker_id": "model-123", "result": {...}},
            "id": "model-123_1696704000000000",
            "timestamp": "2025-10-07T01:00:00.000000Z",
            "correlation_id": "req_abc123"
        }
    """
    message: dict[str, Any] = {
        "signal": signal,
        "payload": payload,
        "id": message_id or generate_message_id(worker_id),
        "timestamp": datetime.now().isoformat() + "Z",
    }

    if correlation_id:
        message["correlation_id"] = correlation_id

    return message


def validate_message(
    message: dict[str, Any], require_correlation_id: bool = False
) -> None:
    """
    Validate message structure.

    Args:
        message: Message to validate
        require_correlation_id: If True, require correlation_id to be present

    Raises:
        ValidationError: If message structure is invalid
    """
    missing_fields: list[str] = []

    # Check required fields
    if "signal" not in message:
        missing_fields.append("signal")
    elif not isinstance(message["signal"], str):
        raise ValidationError(
            "Field 'signal' must be a string",
            {
                "signal": message.get("signal"),
                "type": type(message.get("signal")).__name__,
            },
        )

    if "payload" not in message:
        missing_fields.append("payload")
    elif not isinstance(message["payload"], dict):
        raise ValidationError(
            "Field 'payload' must be a dictionary",
            {"payload_type": type(message.get("payload")).__name__},
        )

    if "id" not in message:
        missing_fields.append("id")
    elif not isinstance(message["id"], str):
        raise ValidationError(
            "Field 'id' must be a string",
            {"id": message.get("id"), "type": type(message.get("id")).__name__},
        )

    if "timestamp" not in message:
        missing_fields.append("timestamp")
    elif not isinstance(message["timestamp"], str):
        raise ValidationError(
            "Field 'timestamp' must be a string",
            {
                "timestamp": message.get("timestamp"),
                "type": type(message.get("timestamp")).__name__,
            },
        )

    if missing_fields:
        raise ValidationError(
            f"Message missing required fields: {', '.join(missing_fields)}",
            {"missing_fields": missing_fields, "message": message},
        )

    # Check correlation_id if required
    if require_correlation_id and "correlation_id" not in message:
        raise ValidationError(
            "Message missing required field: correlation_id", {"message": message}
        )

    # Validate signal is known (optional warning, not error)
    signal = message["signal"]
    if signal not in signals.ALL_SIGNALS:
        # Log warning but don't raise error (allow custom signals)

        get_logger("process_ipc.messages").warning(
            f"Unknown signal '{signal}' - not in predefined signal list"
        )


def extract_from_payload(
    message: dict[str, Any], key: str, default: Any = None, required: bool = False
) -> Any:
    """
    Extract a value from message payload with validation.

    Args:
        message: Message to extract from
        key: Key to extract from payload
        default: Default value if key not found
        required: If True, raise error if key not found

    Returns:
        Any: Extracted value or default

    Raises:
        ValidationError: If required key is missing
    """
    if "payload" not in message:
        if required:
            raise ValidationError(
                f"Cannot extract '{key}': message has no payload", {"message": message}
            )
        return default

    payload = message["payload"]
    if key not in payload:
        if required:
            raise ValidationError(
                f"Required field '{key}' not found in payload",
                {"payload": payload, "required_key": key},
            )
        return default

    return payload[key]


def get_worker_id(message: dict[str, Any]) -> str | None:
    """
    Extract worker_id from message payload.

    Args:
        message: Message to extract from

    Returns:
        Optional[str]: Worker ID or None if not present
    """
    return extract_from_payload(message, "worker_id", default=None, required=False)


def get_correlation_id(message: dict[str, Any]) -> str | None:
    """
    Extract correlation_id from message (top-level).

    Args:
        message: Message to extract from

    Returns:
        Optional[str]: Correlation ID or None if not present
    """
    return message.get("correlation_id")


def add_correlation_id(message: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    """
    Add correlation_id to a message.

    Args:
        message: Message to modify
        correlation_id: Correlation ID to add

    Returns:
        Dict[str, Any]: Modified message (same object, for chaining)
    """
    message["correlation_id"] = correlation_id
    return message
