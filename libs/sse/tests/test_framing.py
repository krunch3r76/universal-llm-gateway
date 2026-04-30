"""Unit tests for libs/sse/framing.py.

Coverage targets:
    - Single complete event in one chunk
    - Single event split across two chunks
    - Multiple events in one chunk
    - Trailing event without final boundary
    - Comment lines (`: ping`)
    - Multi-line `data:` fields
    - `event:`, `id:`, `retry:` fields
    - Non-integer `retry` raises SSEParseError
    - Empty chunks ignored
    - Real Anthropic-shape sample (recorded fixture or inline literal)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sse.framing import iter_sse_events
from sse.protocols import SSEParseError


async def _aiter(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_single_event_in_one_chunk() -> None:
    events = [event async for event in iter_sse_events(_aiter(b"data: hello\n\n"))]
    assert len(events) == 1
    assert events[0].data == "hello"
    assert events[0].event is None


@pytest.mark.asyncio
async def test_event_split_across_chunks() -> None:
    events = [
        event async for event in iter_sse_events(_aiter(b"data: hel", b"lo\n\n"))
    ]
    assert len(events) == 1
    assert events[0].data == "hello"


@pytest.mark.asyncio
async def test_multiple_events_in_one_chunk() -> None:
    raw = b"data: one\n\ndata: two\n\ndata: three\n\n"
    events = [event async for event in iter_sse_events(_aiter(raw))]
    assert [e.data for e in events] == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_trailing_event_without_boundary() -> None:
    """End-of-stream should still yield a trailing event if present."""
    events = [event async for event in iter_sse_events(_aiter(b"data: lonely"))]
    assert len(events) == 1
    assert events[0].data == "lonely"


@pytest.mark.asyncio
async def test_comment_lines_dropped() -> None:
    raw = b": ping\n\ndata: real\n\n"
    events = [event async for event in iter_sse_events(_aiter(raw))]
    assert len(events) == 1
    assert events[0].data == "real"


@pytest.mark.asyncio
async def test_multiline_data_concatenated() -> None:
    raw = b"data: line1\ndata: line2\ndata: line3\n\n"
    events = [event async for event in iter_sse_events(_aiter(raw))]
    assert events[0].data == "line1\nline2\nline3"


@pytest.mark.asyncio
async def test_event_id_retry_fields() -> None:
    raw = b"event: message\nid: 42\nretry: 5000\ndata: payload\n\n"
    events = [event async for event in iter_sse_events(_aiter(raw))]
    assert events[0].event == "message"
    assert events[0].id == "42"
    assert events[0].retry == 5000
    assert events[0].data == "payload"


@pytest.mark.asyncio
async def test_non_integer_retry_raises_parse_error() -> None:
    raw = b"retry: not-a-number\ndata: payload\n\n"
    with pytest.raises(SSEParseError):
        _ = [event async for event in iter_sse_events(_aiter(raw))]


@pytest.mark.asyncio
async def test_empty_chunks_ignored() -> None:
    raw_chunks = (b"", b"data: hi\n\n", b"", b"")
    events = [event async for event in iter_sse_events(_aiter(*raw_chunks))]
    assert len(events) == 1
    assert events[0].data == "hi"


@pytest.mark.asyncio
async def test_anthropic_shape_sample() -> None:
    """Realistic Anthropic SSE shape — message_start + content_block_delta + message_stop."""
    raw = (
        b"event: message_start\n"
        b'data: {"type":"message_start","message":{"id":"msg_abc"}}\n\n'
        b"event: content_block_delta\n"
        b'data: {"type":"content_block_delta","delta":{"text":"Hello"}}\n\n'
        b"event: message_stop\n"
        b'data: {"type":"message_stop"}\n\n'
    )
    events = [event async for event in iter_sse_events(_aiter(raw))]
    assert len(events) == 3
    assert events[0].event == "message_start"
    assert events[1].event == "content_block_delta"
    assert events[2].event == "message_stop"
    assert '"text":"Hello"' in events[1].data


@pytest.mark.asyncio
async def test_field_without_colon() -> None:
    """Per W3C, a line with no `:` is treated as a field with empty value."""
    raw = b"event\ndata: payload\n\n"
    events = [event async for event in iter_sse_events(_aiter(raw))]
    # `event` field set to empty string per spec.
    assert events[0].event == ""
    assert events[0].data == "payload"
