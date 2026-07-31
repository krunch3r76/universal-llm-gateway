"""Unit tests for AnthropicReducer — text and tool-use flows.

Coverage:
- Simple text response (synthetic events)
- Tool-use response: full three-event cycle (content_block_start → delta × N → stop)
- Partial-JSON accumulation for multi-chunk tool input
- to_terminal_dict shape + parse_frontier_response integration

Error handling and fixture integration: ``test_anthropic_reducer_errors.py``
"""

from __future__ import annotations

import json
from typing import Any

from sse.core import SSEMessage

from llm_adapters.streaming.anthropic import AnthropicReducer, _AnthropicState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(event_type: str, payload: dict[str, Any]) -> SSEMessage:
    return SSEMessage(
        data=json.dumps({"type": event_type, **payload}),
        event=event_type,
    )


def _drive(events: list[SSEMessage]) -> _AnthropicState:
    r = AnthropicReducer()
    state = r.initial_state()
    for evt in events:
        done = r.reduce(state, evt)
        if done:
            break
    return state


# ---------------------------------------------------------------------------
# Synthetic event sequences
# ---------------------------------------------------------------------------

TEXT_EVENTS: list[SSEMessage] = [
    _msg(
        "message_start",
        {
            "message": {
                "id": "msg_1",
                "model": "claude-haiku-4-5",
                "usage": {"input_tokens": 10},
            },
        },
    ),
    _msg("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
    _msg(
        "content_block_delta",
        {"index": 0, "delta": {"type": "text_delta", "text": "Hello"}},
    ),
    _msg(
        "content_block_delta",
        {"index": 0, "delta": {"type": "text_delta", "text": " world"}},
    ),
    _msg("content_block_stop", {"index": 0}),
    _msg(
        "message_delta",
        {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
    ),
    _msg("message_stop", {}),
]

TOOL_USE_EVENTS: list[SSEMessage] = [
    _msg(
        "message_start",
        {
            "message": {
                "id": "msg_2",
                "model": "claude-haiku-4-5",
                "usage": {"input_tokens": 580},
            },
        },
    ),
    _msg(
        "content_block_start",
        {
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_abc123",
                "name": "get_weather",
                "input": {},
            },
        },
    ),
    _msg(
        "content_block_delta",
        {
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": ""},
        },
    ),
    _msg(
        "content_block_delta",
        {
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"locati'},
        },
    ),
    _msg(
        "content_block_delta",
        {
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": 'on": "Paris"}'},
        },
    ),
    _msg("content_block_stop", {"index": 0}),
    _msg(
        "message_delta",
        {
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 54},
        },
    ),
    _msg("message_stop", {}),
]


# ---------------------------------------------------------------------------
# Text flow
# ---------------------------------------------------------------------------


def test_text_content_accumulated() -> None:
    state = _drive(TEXT_EVENTS)
    assert len(state.content) == 1
    assert state.content[0] == {"type": "text", "text": "Hello world"}


def test_text_usage_populated() -> None:
    state = _drive(TEXT_EVENTS)
    assert state.usage["input_tokens"] == 10
    assert state.usage["output_tokens"] == 5


def test_text_to_terminal_dict() -> None:
    state = _drive(TEXT_EVENTS)
    d = AnthropicReducer.to_terminal_dict(state)
    assert d["content"] == [{"type": "text", "text": "Hello world"}]
    assert d["stop_reason"] == "end_turn"
    assert d["model"] == "claude-haiku-4-5"


def test_text_parse_frontier_response() -> None:
    from llm_adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)  # skip __init__; no key needed
    state = _drive(TEXT_EVENTS)
    parsed = adapter.parse_frontier_response(AnthropicReducer.to_terminal_dict(state))
    assert parsed["content"] == "Hello world"
    assert parsed["tool_calls"] is None
    assert parsed["usage"]["input_tokens"] == 10
    assert parsed["usage"]["output_tokens"] == 5


# ---------------------------------------------------------------------------
# Tool-use flow (the critical path)
# ---------------------------------------------------------------------------


def test_tool_use_block_assembled() -> None:
    """AnthropicReducer must accumulate fragmented input_json_delta chunks."""
    state = _drive(TOOL_USE_EVENTS)
    assert len(state.content) == 1
    block = state.content[0]
    assert block["type"] == "tool_use"
    assert block["id"] == "toolu_abc123"
    assert block["name"] == "get_weather"
    assert block["input"] == {"location": "Paris"}


def test_tool_use_stop_reason() -> None:
    state = _drive(TOOL_USE_EVENTS)
    assert state.stop_reason == "tool_use"


def test_tool_use_usage() -> None:
    state = _drive(TOOL_USE_EVENTS)
    assert state.usage["input_tokens"] == 580
    assert state.usage["output_tokens"] == 54


def test_tool_use_to_terminal_dict() -> None:
    state = _drive(TOOL_USE_EVENTS)
    d = AnthropicReducer.to_terminal_dict(state)
    assert d["content"] == [
        {
            "type": "tool_use",
            "id": "toolu_abc123",
            "name": "get_weather",
            "input": {"location": "Paris"},
        }
    ]
    assert d["stop_reason"] == "tool_use"


def test_tool_use_parse_frontier_response() -> None:
    """parse_frontier_response must extract tool_calls from the assembled block."""
    from llm_adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    state = _drive(TOOL_USE_EVENTS)
    parsed = adapter.parse_frontier_response(AnthropicReducer.to_terminal_dict(state))
    assert parsed["content"] == ""
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) == 1
    tc = tool_calls[0]
    assert tc["name"] == "get_weather"
    assert tc["input"] == {"location": "Paris"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_tool_input_json() -> None:
    """Empty partial_json sequence produces an empty dict input, not an error."""
    events = [
        _msg(
            "message_start",
            {"message": {"id": "m3", "model": "m", "usage": {"input_tokens": 1}}},
        ),
        _msg(
            "content_block_start",
            {
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "id1",
                    "name": "noop",
                    "input": {},
                },
            },
        ),
        _msg(
            "content_block_delta",
            {"index": 0, "delta": {"type": "input_json_delta", "partial_json": ""}},
        ),
        _msg("content_block_stop", {"index": 0}),
        _msg("message_stop", {}),
    ]
    state = _drive(events)
    assert state.content[0]["input"] == {}


def test_invalid_json_input_degrades_gracefully() -> None:
    """Broken partial JSON does not raise; falls back to empty input dict."""
    events = [
        _msg(
            "message_start",
            {"message": {"id": "m4", "model": "m", "usage": {"input_tokens": 1}}},
        ),
        _msg(
            "content_block_start",
            {
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "id2",
                    "name": "tool",
                    "input": {},
                },
            },
        ),
        _msg(
            "content_block_delta",
            {
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": "{broken"},
            },
        ),
        _msg("content_block_stop", {"index": 0}),
        _msg("message_stop", {}),
    ]
    state = _drive(events)
    assert state.content[0]["input"] == {}
