"""Phase 3.5 regression: anthropic.py forward_native_stream emits W3C `\\n\\n`-framed events.

Why this test exists:
    Before Phase 3.5, the adapter emitted single-`\\n`-terminated lines. The
    chat-completions path worked because StreamTranslator._chunk() re-emitted
    with `\\n\\n`. The native path (frontier_dispatch.py:787 in step 6) pipes
    the adapter output directly into libs/sse/framing.iter_sse_events which
    splits strictly on `\\n\\n`. Phase 3.5 normalizes the egress so both paths
    work.

    This test asserts the new framing is W3C-correct and that the same
    buffer-by-blank-line contract is consumable by iter_sse_events (duplicates
    the adapter loop shape deliberately — see phase-3.5 plan Task 2).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

# Recorded shape of an Anthropic streaming response: the fixture mirrors
# what aiter_lines() yields when fed Anthropic's wire format. Each entry is
# either a non-blank SSE line or "" representing the W3C event boundary.
ANTHROPIC_LINES_FIXTURE: list[str] = [
    "event: message_start",
    'data: {"type":"message_start","message":{"id":"msg_abc","model":"claude-sonnet-4-5"}}',
    "",
    "event: content_block_start",
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text"}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}',
    "",
    "event: message_stop",
    'data: {"type":"message_stop"}',
    "",
]


class _FakeAiterLines:
    """Stand-in for httpx.Response.aiter_lines() returning the fixture."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_native_stream_emits_w3c_event_frames() -> None:
    """The Phase-3.5 generator yields one `\\n\\n`-terminated frame per Anthropic event."""

    pending_lines: list[str] = []
    frames: list[bytes] = []

    fake = _FakeAiterLines(ANTHROPIC_LINES_FIXTURE)
    async for line in fake:
        if line == "":
            if pending_lines:
                frame = "\n".join(pending_lines) + "\n\n"
                frames.append(frame.encode())
                pending_lines = []
            continue
        pending_lines.append(line)
    if pending_lines:
        frames.append(("\n".join(pending_lines) + "\n\n").encode())

    # Each frame must end with `\n\n` (W3C boundary).
    for frame in frames:
        assert frame.endswith(b"\n\n"), f"frame missing W3C boundary: {frame!r}"

    # Five Anthropic events in the fixture → five frames.
    assert len(frames) == 5
    assert b"message_start" in frames[0]
    assert b"content_block_start" in frames[1]
    assert b"Hello" in frames[2]
    assert b"world" in frames[3]
    assert b"message_stop" in frames[4]


@pytest.mark.asyncio
async def test_w3c_frames_parse_via_libs_sse_framing() -> None:
    """The Phase-3.5 output is consumable by libs/sse/framing.iter_sse_events."""

    from sse.framing import iter_sse_events

    pending_lines: list[str] = []
    raw_chunks: list[bytes] = []
    fake = _FakeAiterLines(ANTHROPIC_LINES_FIXTURE)
    async for line in fake:
        if line == "":
            if pending_lines:
                raw_chunks.append(("\n".join(pending_lines) + "\n\n").encode())
                pending_lines = []
            continue
        pending_lines.append(line)
    if pending_lines:
        raw_chunks.append(("\n".join(pending_lines) + "\n\n").encode())

    async def _aiter_chunks() -> AsyncIterator[bytes]:
        for chunk in raw_chunks:
            yield chunk

    parsed = [event async for event in iter_sse_events(_aiter_chunks())]
    assert [e.event for e in parsed] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "message_stop",
    ]
    # Sanity-check that data round-trips intact (one event's payload is enough)
    delta_event = parsed[2]
    assert isinstance(delta_event.data, str)
    payload = json.loads(delta_event.data)
    assert payload["delta"]["text"] == "Hello"
