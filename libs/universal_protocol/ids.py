"""Stream and request ID generation for Universal Protocol.

IDs are used to correlate WebSocket streams and JSON-RPC requests.
Both use a simple prefix + uuid4 format for uniqueness and readability.
"""

import uuid


def generate_stream_id(prefix: str = "stream") -> str:
    """Generate a unique stream ID.

    Format: "{prefix}-{uuid4}"

    Args:
        prefix: Optional prefix (default: "stream")
                Used to identify stream type in logs

    Returns:
        Unique stream ID (e.g., "stream-550e8400-e29b-41d4-a716-446655440000")

    Example:
        >>> sid = generate_stream_id()
        >>> sid.startswith("stream-")
        True
        >>> len(sid.split("-")[1]) == 36  # UUID length
        True
    """
    stream_uuid = str(uuid.uuid4())
    return f"{prefix}-{stream_uuid}"


def generate_request_id(prefix: str = "req") -> str:
    """Generate a unique request ID.

    Format: "{prefix}-{uuid4}"

    Args:
        prefix: Optional prefix (default: "req")
                Used to identify request type in logs

    Returns:
        Unique request ID (e.g., "req-550e8400-e29b-41d4-a716-446655440000")

    Example:
        >>> rid = generate_request_id()
        >>> rid.startswith("req-")
        True
        >>> len(rid.split("-")[1]) == 36  # UUID length
        True
    """
    request_uuid = str(uuid.uuid4())
    return f"{prefix}-{request_uuid}"


def generate_id(prefix: str) -> str:
    """Generate a unique ID with arbitrary prefix.

    Generic ID generation for use cases beyond streams and requests.

    Args:
        prefix: Identifier prefix (e.g., "task", "batch", "session")

    Returns:
        Unique ID (e.g., "task-550e8400-e29b-41d4-a716-446655440000")

    Example:
        >>> task_id = generate_id("task")
        >>> task_id.startswith("task-")
        True
    """
    unique_part = str(uuid.uuid4())
    return f"{prefix}-{unique_part}"
