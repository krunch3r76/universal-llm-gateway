"""Matched-pair fixtures for Anthropic streaming reducer terminal shape."""

from __future__ import annotations

import asyncio

import pytest
from llm_adapters.anthropic import AnthropicAdapter
from llm_adapters.streaming.anthropic import AnthropicReducer
from sse.accumulator import accumulate_sse_stream
from sse.protocols import SSEProviderError


def _reduce(lines: list[str]) -> dict:
    frames: list[bytes] = []
    buf: list[str] = []
    for line in lines:
        if line == "":
            if buf:
                frames.append(("\n".join(buf) + "\n\n").encode())
                buf.clear()
            continue
        buf.append(line)
    if buf:
        frames.append(("\n".join(buf) + "\n\n").encode())

    async def _iter():
        for frame in frames:
            yield frame

    async def _run() -> dict:
        reducer = AnthropicReducer()
        state = await accumulate_sse_stream(_iter(), reducer)
        return AnthropicReducer.to_terminal_dict(state)

    return asyncio.run(_run())


def _parse(terminal: dict) -> dict:
    adapter = AnthropicAdapter(api_key="k-test")
    return adapter.parse_frontier_response(terminal)


def test_reduce_text_only_fixture() -> None:
    terminal = _reduce(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"id":"msg_t1","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"stop_reason":null,"usage":{"input_tokens":10,"output_tokens":0}}}',
            "",
            "event: content_block_start",
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
        ]
    )
    assert terminal == {
        "id": "msg_t1",
        "model": "claude-sonnet-4-6",
        "stop_reason": "end_turn",
        "type": "message",
        "content": [{"type": "text", "text": "Hello world"}],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "thinking_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }
    assert _parse(terminal)["content"] == "Hello world"


def test_reduce_tool_use_fixture() -> None:
    terminal = _reduce(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"id":"msg_t2","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"stop_reason":null,"usage":{"input_tokens":20,"output_tokens":0}}}',
            "",
            "event: content_block_start",
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"I will search"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: content_block_start",
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_abc","name":"search","input":{}}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"q\\""}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":":\\"hello\\"}"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":1}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":8}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
        ]
    )
    assert terminal == {
        "id": "msg_t2",
        "model": "claude-sonnet-4-6",
        "stop_reason": "tool_use",
        "type": "message",
        "content": [
            {"type": "text", "text": "I will search"},
            {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "search",
                "input": {"q": "hello"},
            },
        ],
        "usage": {
            "input_tokens": 20,
            "output_tokens": 8,
            "thinking_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }
    parsed = _parse(terminal)
    assert parsed["tool_calls"] == [
        {"id": "toolu_abc", "name": "search", "input": {"q": "hello"}}
    ]


def test_reduce_thinking_fixture() -> None:
    terminal = _reduce(
        [
            "event: message_start",
            'data: {"type":"message_start","message":{"id":"msg_t3","type":"message","role":"assistant","model":"claude-sonnet-4-6","content":[],"stop_reason":null,"usage":{"input_tokens":11,"output_tokens":0}}}',
            "",
            "event: content_block_start",
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":null}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"Need to call tool."}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig_123"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":0}',
            "",
            "event: content_block_start",
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
            "",
            "event: content_block_delta",
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"Done"}}',
            "",
            "event: content_block_stop",
            'data: {"type":"content_block_stop","index":1}',
            "",
            "event: message_delta",
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3,"thinking_tokens":7}}',
            "",
            "event: message_stop",
            'data: {"type":"message_stop"}',
        ]
    )
    assert terminal["content"][0] == {
        "type": "thinking",
        "thinking": "Need to call tool.",
        "signature": "sig_123",
    }
    parsed = _parse(terminal)
    assert parsed["thinking"]["text"] == "Need to call tool."


def test_reduce_error_fixture_raises_provider_error() -> None:
    with pytest.raises(SSEProviderError):
        _reduce(
            [
                "event: error",
                'data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            ]
        )
