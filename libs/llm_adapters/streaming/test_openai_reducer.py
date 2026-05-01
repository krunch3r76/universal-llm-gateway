"""Unit tests for OpenAIResponsesReducer — reduce events and terminal dict.

Coverage:
- Simple text response (synthetic events)
- Function call response (synthetic events)
- Reasoning item (xAI grok-mini style)
- SSEProviderError on stream-level error event
- to_terminal_dict on empty state (graceful no-op)

Fixture integration tests: ``test_openai_reducer_fixtures.py``
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sse.core import SSEMessage
from sse.protocols import SSEProviderError

from llm_adapters.streaming.openai import OpenAIResponsesReducer, _OpenAIResponsesState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(event_type: str, payload: dict[str, Any]) -> SSEMessage:
    return SSEMessage(
        data=json.dumps({"type": event_type, **payload}), event=event_type
    )


def _drive(events: list[SSEMessage]) -> _OpenAIResponsesState:
    """Drive reducer over a list of synthetic events, return terminal state."""
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    for evt in events:
        done = r.reduce(state, evt)
        if done:
            break
    return state


SIMPLE_RESPONSE = {
    "id": "resp_abc123",
    "object": "response",
    "status": "completed",
    "output": [
        {
            "type": "message",
            "id": "msg_abc123",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Hello!"}],
        }
    ],
    "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    "model": "gpt-4o-mini-2024-07-18",
}

FUNCTION_CALL_RESPONSE = {
    "id": "resp_def456",
    "object": "response",
    "status": "completed",
    "output": [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "cortex",
            "arguments": '{"query": "test"}',
        }
    ],
    "usage": {"input_tokens": 20, "output_tokens": 15, "total_tokens": 35},
    "model": "gpt-4o-mini-2024-07-18",
}

REASONING_RESPONSE = {
    "id": "resp_ghi789",
    "object": "response",
    "status": "completed",
    "output": [
        {
            "type": "reasoning",
            "id": "rs_ghi789",
            "summary": [],
            "status": "completed",
            "content": [{"type": "reasoning_text", "text": "The user wants hello."}],
        },
        {
            "type": "message",
            "id": "msg_ghi789",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "hello"}],
        },
    ],
    "usage": {
        "input_tokens": 11,
        "output_tokens": 176,
        "output_tokens_details": {"reasoning_tokens": 175},
        "total_tokens": 187,
    },
    "model": "grok-3-mini",
}


# ---------------------------------------------------------------------------
# Unit: reduce() returns False for non-terminal events
# ---------------------------------------------------------------------------


def test_reduce_ignores_created() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    evt = _msg("response.created", {"response": {"id": "r1", "status": "in_progress"}})
    assert r.reduce(state, evt) is False
    assert state.terminal_response == {}


def test_reduce_ignores_in_progress() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    evt = _msg(
        "response.in_progress", {"response": {"id": "r1", "status": "in_progress"}}
    )
    assert r.reduce(state, evt) is False


def test_reduce_ignores_text_delta() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    evt = _msg("response.output_text.delta", {"delta": "Hello", "output_index": 0})
    assert r.reduce(state, evt) is False


def test_reduce_ignores_output_item_done() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    evt = _msg(
        "response.output_item.done", {"item": {"type": "message"}, "output_index": 0}
    )
    assert r.reduce(state, evt) is False


# ---------------------------------------------------------------------------
# Unit: response.output_item.added tracking
# ---------------------------------------------------------------------------


def test_reduce_tracks_function_call_item() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    item = {"type": "function_call", "id": "fc_1", "name": "cortex", "arguments": ""}
    evt = _msg("response.output_item.added", {"item": item, "output_index": 0})
    r.reduce(state, evt)
    assert state.pending_items[0]["name"] == "cortex"
    assert state.pending_items[0]["type"] == "function_call"


def test_reduce_tracks_message_item() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    item = {"type": "message", "id": "msg_1", "role": "assistant", "content": []}
    evt = _msg("response.output_item.added", {"item": item, "output_index": 0})
    r.reduce(state, evt)
    assert state.pending_items[0]["type"] == "message"


# ---------------------------------------------------------------------------
# Unit: response.completed terminates stream with correct state
# ---------------------------------------------------------------------------


def test_reduce_completed_saves_response_and_terminates() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    evt = _msg("response.completed", {"response": SIMPLE_RESPONSE})
    terminal = r.reduce(state, evt)
    assert terminal is True
    # The reducer JSON-round-trips the payload, so equality not identity.
    assert state.terminal_response["id"] == SIMPLE_RESPONSE["id"]
    assert state.terminal_response["usage"] == SIMPLE_RESPONSE["usage"]


def test_to_terminal_dict_returns_saved_response() -> None:
    state = _drive(
        [
            _msg("response.created", {"response": {}}),
            _msg("response.output_text.delta", {"delta": "H", "output_index": 0}),
            _msg("response.completed", {"response": SIMPLE_RESPONSE}),
        ]
    )
    result = OpenAIResponsesReducer.to_terminal_dict(state)
    assert result["output"][0]["content"][0]["text"] == "Hello!"
    assert result["usage"]["input_tokens"] == 11
    assert result["usage"]["output_tokens"] == 3
    assert result["model"] == "gpt-4o-mini-2024-07-18"


def test_to_terminal_dict_empty_state_returns_empty_dict() -> None:
    state = OpenAIResponsesReducer().initial_state()
    assert OpenAIResponsesReducer.to_terminal_dict(state) == {}


# ---------------------------------------------------------------------------
# Unit: parse_frontier_response integration (OpenAI shape)
# ---------------------------------------------------------------------------


def test_terminal_dict_parses_correctly_simple_text() -> None:
    from llm_adapters.responses import ResponsesAPIAdapter

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.openai.com/v1", vendor="openai"
    )
    state = _drive([_msg("response.completed", {"response": SIMPLE_RESPONSE})])
    terminal = OpenAIResponsesReducer.to_terminal_dict(state)
    parsed = adapter.parse_frontier_response(terminal)
    assert parsed["content"] == "Hello!"
    assert parsed["usage"]["input_tokens"] == 11
    assert parsed["usage"]["output_tokens"] == 3
    assert parsed["provider"] == "openai"


def test_terminal_dict_parses_correctly_function_call() -> None:
    from llm_adapters.responses import ResponsesAPIAdapter

    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.openai.com/v1", vendor="openai"
    )
    state = _drive([_msg("response.completed", {"response": FUNCTION_CALL_RESPONSE})])
    terminal = OpenAIResponsesReducer.to_terminal_dict(state)
    parsed = adapter.parse_frontier_response(terminal)
    assert parsed["content"] == ""
    tool_calls = parsed.get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "cortex"
    assert tool_calls[0]["arguments"] == '{"query": "test"}'


def test_terminal_dict_parses_correctly_reasoning_xai() -> None:
    from llm_adapters.responses import ResponsesAPIAdapter

    # xAI vendor — same adapter, different vendor label
    adapter = ResponsesAPIAdapter(
        api_key="k", base_url="https://api.x.ai/v1", vendor="xai"
    )
    state = _drive([_msg("response.completed", {"response": REASONING_RESPONSE})])
    terminal = OpenAIResponsesReducer.to_terminal_dict(state)
    parsed = adapter.parse_frontier_response(terminal)
    assert parsed["content"] == "hello"
    assert parsed["usage"]["input_tokens"] == 11
    assert parsed["usage"]["output_tokens"] == 176


# ---------------------------------------------------------------------------
# Unit: terminal_error raises SSEProviderError
# ---------------------------------------------------------------------------


def test_terminal_error_raises_provider_error() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    err_evt = SSEMessage(data='{"error": {"message": "rate limit"}}', event="error")
    with pytest.raises(SSEProviderError, match="OpenAI Responses stream error"):
        r.terminal_error(state, err_evt)


def test_terminal_error_handles_malformed_data() -> None:
    r = OpenAIResponsesReducer()
    state = r.initial_state()
    err_evt = SSEMessage(data="not-json", event="error")
    with pytest.raises(SSEProviderError):
        r.terminal_error(state, err_evt)
