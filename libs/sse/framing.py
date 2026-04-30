"""W3C-compliant SSE byte-stream → SSEMessage iterator.

Pure RFC handling. No vendor knowledge. No reducer involvement.

Source bytes are buffered until an event boundary (`\\n\\n`) is observed; each
complete event is parsed into a typed SSEMessage. Trailing bytes without a
boundary are emitted at end-of-stream if non-empty.

Designed for httpx.Response.aiter_raw() input but works with any
AsyncIterator[bytes].
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sse.core import SSEMessage
from sse.protocols import SSEParseError

EVENT_BOUNDARY = b"\n\n"


async def iter_sse_events(byte_iter: AsyncIterator[bytes]) -> AsyncIterator[SSEMessage]:
    """Buffer bytes across W3C event boundaries; yield SSEMessage per event.

    Strict W3C framing: events are terminated by `\\n\\n`. Single-`\\n`-only
    framing is not tolerated — cloud-proxy adapter egress that uses single-`\\n`
    is normalized to `\\n\\n` upstream (Phase 3.5).

    Args:
        byte_iter: Async iterator producing arbitrary-sized chunks of SSE bytes.
            Typical source: ``httpx.Response.aiter_raw()``.

    Yields:
        SSEMessage objects, one per dispatched event. Per W3C, frames whose
        data buffer is empty after parsing (comment-only frames, retry-only
        frames) are NOT dispatched — they are silently skipped.

    Raises:
        SSEParseError: If a complete event frame fails to parse (malformed
            field, non-integer retry, etc.).
    """
    buffer = b""
    async for chunk in byte_iter:
        if not chunk:
            continue
        buffer += chunk
        while EVENT_BOUNDARY in buffer:
            raw_event, buffer = buffer.split(EVENT_BOUNDARY, 1)
            if not raw_event.strip():
                continue
            parsed = _parse_event(raw_event)
            if parsed is not None:
                yield parsed
    # End-of-stream: yield any trailing event without a final boundary.
    if buffer.strip():
        parsed = _parse_event(buffer)
        if parsed is not None:
            yield parsed


def _parse_event(raw: bytes) -> SSEMessage | None:
    """Parse one W3C SSE event frame into an SSEMessage.

    Field order in the frame is irrelevant. Multiple `data:` lines are
    concatenated with `\\n` per W3C spec. Comment lines (starting with `:`)
    are silently dropped.

    Per W3C dispatch rules, a frame with no `data:` line (comment-only or
    retry-only) does NOT dispatch an event — this returns ``None`` in that
    case so the caller can skip it.

    Args:
        raw: Raw event frame bytes (without the trailing `\\n\\n`).

    Returns:
        SSEMessage with parsed fields, or ``None`` if the frame had no
        `data:` line (W3C: do not dispatch). ``data`` is always a string in
        this path (no JSON parsing — that's `parse_sse_message`'s job).

    Raises:
        SSEParseError: If `retry:` value is not an integer, or any other
            parse-stage failure.
    """
    text = raw.decode("utf-8", errors="replace")
    event: str | None = None
    data_lines: list[str] = []
    msg_id: str | None = None
    retry: int | None = None

    for line in text.split("\n"):
        if not line or line.startswith(":"):
            # Comment line or blank line inside a multi-line event (we already
            # split on `\n\n`, so blank-inside-frame shouldn't happen, but be
            # defensive).
            continue
        if ":" not in line:
            # Field without value — W3C spec treats as field with empty value.
            field_name = line.rstrip()
            value = ""
        else:
            field_name, _, value = line.partition(":")
            # Optional single space after colon, per spec.
            if value.startswith(" "):
                value = value[1:]
        if field_name == "event":
            event = value
        elif field_name == "data":
            data_lines.append(value)
        elif field_name == "id":
            msg_id = value
        elif field_name == "retry":
            try:
                retry = int(value)
            except ValueError as exc:
                raise SSEParseError(f"non-integer retry value: {value!r}") from exc
        # Unknown fields per W3C: silently ignored.

    if not data_lines:
        # W3C: do not dispatch when data buffer is empty (comment-only,
        # retry-only, or event-only frames).
        return None

    return SSEMessage(
        data="\n".join(data_lines),
        event=event,
        id=msg_id,
        retry=retry,
    )
