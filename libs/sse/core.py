"""SSE (Server-Sent Events) core protocol implementation.

This module provides exact SSE formatting and parsing per RFC 9110 / W3C spec.
Format: "data: {json}\n\n" (literal double newline)

Two APIs:
- Typed API: `SSEMessage` + `format_sse_message()` / `parse_sse_message()`
  Supports event types, multiline data, all W3C fields.
- JSON-only API: `format_sse()` / `parse_sse()`
  For dict-only payloads (inference streaming). Does NOT support multiline.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

JSONDict = dict[str, object]
JSONData = str | JSONDict


@dataclass
class SSEMessage:
    """Typed SSE message with all standard fields per W3C spec.

    Attributes:
        data: Message payload - must be str or dict[str, Any].
        event: Event type (e.g., "log", "complete", "error").
        id: Event ID for reconnection.
        retry: Reconnection timeout in milliseconds.
    """

    data: JSONData
    event: str | None = None
    id: str | None = None
    retry: int | None = None


def format_sse_message(msg: SSEMessage) -> str:
    """Format SSE message with all fields per W3C spec.

    Field order: event, id, retry, data (standard ordering).
    Messages terminated with double newline.

    Per W3C spec, multiline string data uses multiple `data:` lines:
    - Input: "line1\\nline2"
    - Output: "data: line1\\ndata: line2\\n\\n"

    Args:
        msg: SSEMessage with data (str or dict) and optional event/id/retry

    Returns:
        Formatted SSE message string

    Raises:
        TypeError: If msg.data is not str or dict

    Example:
        >>> format_sse_message(SSEMessage(event="log", data="Hello"))
        'event: log\\ndata: Hello\\n\\n'

        >>> format_sse_message(SSEMessage(data="line1\\nline2"))
        'data: line1\\ndata: line2\\n\\n'
    """
    lines: list[str] = []
    if msg.event:
        lines.append(f"event: {msg.event}")
    if msg.id:
        lines.append(f"id: {msg.id}")
    if msg.retry is not None:
        lines.append(f"retry: {msg.retry}")

    if not isinstance(msg.data, (Mapping, str)):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError(
            f"SSEMessage.data must be str or dict, got {type(msg.data).__name__}"
        )

    if isinstance(msg.data, Mapping):
        # Avoid dict() copy when already a dict
        serializable = msg.data if isinstance(msg.data, dict) else dict(msg.data)
        data_str = json.dumps(serializable, separators=(",", ":"))
        lines.append(f"data: {data_str}")
    else:
        # String data: split on newlines, each gets own data: prefix
        data_str = cast(str, msg.data)  # pyright: ignore[reportUnnecessaryCast]
        for data_line in data_str.split("\n"):
            lines.append(f"data: {data_line}")

    return "\n".join(lines) + "\n\n"


def parse_sse_message(raw: str) -> SSEMessage:
    """Parse raw SSE message into typed structure.

    Extracts event, data, id, retry fields from SSE format.
    Multiple `data:` lines are concatenated with newlines per W3C spec.
    Attempts JSON decode on final data string.

    Args:
        raw: Raw SSE message (may include multiple field lines)

    Returns:
        SSEMessage with parsed fields

    Raises:
        ValueError: If message has no data field

    Example:
        >>> parse_sse_message("event: log\\ndata: Hello")
        SSEMessage(data='Hello', event='log', id=None, retry=None)

        >>> parse_sse_message("data: line1\\ndata: line2")
        SSEMessage(data='line1\\nline2', event=None, id=None, retry=None)
    """
    event = None
    data_lines: list[str] = []
    msg_id = None
    retry = None

    for line in raw.strip().split("\n"):
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data_lines.append(line[6:])
        elif line.startswith("id: "):
            msg_id = line[4:]
        elif line.startswith("retry: "):
            retry = int(line[7:])

    if not data_lines:
        raise ValueError("SSE message missing data field")

    # Concatenate multiple data lines with newlines per W3C spec
    data: JSONData = "\n".join(data_lines)

    # Attempt JSON decode - only accept dict, otherwise keep as string
    try:
        loaded: object = json.loads(data)
        if isinstance(loaded, Mapping):
            data = dict(cast(Mapping[str, object], loaded))
        # Non-dict JSON (arrays, numbers, booleans, null) kept as original string
    except json.JSONDecodeError:
        pass  # Keep as string

    return SSEMessage(data=data, event=event, id=msg_id, retry=retry)


def format_sse(data: Mapping[str, object]) -> str:
    """Format dict as SSE message (JSON-only, no event types).

    For inference streaming where payload is always a JSON dict.
    Does NOT support multiline data or event types.
    Use `format_sse_message()` for full W3C SSE support.

    Args:
        data: Dictionary to serialize as compact JSON

    Returns:
        Formatted string: "data: {json}\\n\\n"

    Example:
        >>> format_sse({"t": "token", "i": 42, "txt": "hello"})
        'data: {"t":"token","i":42,"txt":"hello"}\\n\\n'
    """
    # Avoid dict() copy when already a dict (hot-path optimization)
    serializable = data if isinstance(data, dict) else dict(data)
    json_str = json.dumps(serializable, separators=(",", ":"))
    return f"data: {json_str}\n\n"


def parse_sse(message: str) -> JSONDict:
    """Parse SSE message expecting JSON dict payload.

    For inference streaming where payload is always a JSON dict.
    Use `parse_sse_message()` for full W3C SSE support including
    event types and multiline data.

    Args:
        message: Raw SSE message or raw JSON string

    Returns:
        Parsed dict

    Raises:
        ValueError: If message is empty, not a string, or not valid JSON

    Example:
        >>> parse_sse('data: {"t": "token", "i": 42}')
        {'t': 'token', 'i': 42}

        >>> parse_sse('{"t": "token", "i": 42}')  # Raw JSON also accepted
        {'t': 'token', 'i': 42}
    """
    if not message:
        raise ValueError("Message must be non-empty string")

    line = message.strip()
    if line.startswith("data: "):
        line = line[6:]

    if not line:
        raise ValueError("Empty message after removing SSE prefix")

    try:
        loaded: object = cast(object, json.loads(line))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in SSE message: {e}") from e

    if isinstance(loaded, Mapping):
        loaded_mapping = cast(Mapping[str, object], loaded)
        return dict(loaded_mapping)
    raise ValueError("SSE JSON payload must be an object")
