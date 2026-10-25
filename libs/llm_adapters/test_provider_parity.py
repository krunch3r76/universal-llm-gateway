"""Regression tests for provider-parity fixes CP-002, CP-003, CP-005.

CP-003 — Anthropic tool_result.is_error flag for failed tool executions.
CP-005 — finish_reason populated from provider termination metadata.
CP-002 — Responses API append_tool_round replays full prior output
         (message + reasoning[if encrypted] + function_call) before
         appending function_call_output items.
"""

from __future__ import annotations

from typing import Any

from llm_adapters.anthropic.adapter import AnthropicAdapter
from llm_adapters.responses.adapter import ResponsesAPIAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _anthropic_adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="test-key")


def _responses_adapter(vendor: str = "openai") -> ResponsesAPIAdapter:
    return ResponsesAPIAdapter(
        api_key="test-key",
        base_url="https://example.test",
        vendor=vendor,
    )


# ---------------------------------------------------------------------------
# CP-003 — Anthropic is_error flag
# ---------------------------------------------------------------------------


def test_anthropic_append_tool_round_is_error_on_failed_tool() -> None:
    """A tool result with ok=False produces tool_result.is_error=True."""
    adapter = _anthropic_adapter()
    body: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "go"}],
    }
    raw_response: dict[str, Any] = {
        "content": [{"type": "tool_use", "id": "t1", "name": "cortex", "input": {}}],
    }
    tool_results: list[dict[str, Any]] = [
        {
            "id": "t1",
            "name": "cortex",
            "content": '{"error": "not found"}',
            "ok": False,
        },
    ]
    adapter.append_tool_round(body, raw_response, tool_results)
    user_msg = body["messages"][-1]
    assert user_msg["role"] == "user"
    result_block = user_msg["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "t1"
    assert result_block.get("is_error") is True


def test_anthropic_append_tool_round_no_is_error_on_success() -> None:
    """A successful tool result must NOT carry is_error (omit the key entirely)."""
    adapter = _anthropic_adapter()
    body: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "go"}],
    }
    raw_response: dict[str, Any] = {
        "content": [{"type": "tool_use", "id": "t1", "name": "cortex", "input": {}}],
    }
    # ok=True
    tool_results_ok: list[dict[str, Any]] = [
        {"id": "t1", "name": "cortex", "content": '{"result": "ok"}', "ok": True},
    ]
    adapter.append_tool_round(body, raw_response, tool_results_ok)
    result_block = body["messages"][-1]["content"][0]
    assert "is_error" not in result_block

    # ok absent (old callers without the key)
    body2: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "go"}],
    }
    tool_results_no_ok: list[dict[str, Any]] = [
        {"id": "t2", "name": "cortex", "content": '{"result": "ok"}'},
    ]
    adapter.append_tool_round(body2, raw_response, tool_results_no_ok)
    result_block2 = body2["messages"][-1]["content"][0]
    assert "is_error" not in result_block2


# ---------------------------------------------------------------------------
# CP-005 — finish_reason from provider termination metadata
# ---------------------------------------------------------------------------


def test_anthropic_parse_finish_reason_max_tokens() -> None:
    """AnthropicAdapter.parse_frontier_response surfaces stop_reason as finish_reason."""
    adapter = _anthropic_adapter()
    raw: dict[str, Any] = {
        "id": "msg_1",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "truncated"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "max_tokens",
    }
    result = adapter.parse_frontier_response(raw)
    assert result["finish_reason"] == "max_tokens"


def test_anthropic_parse_finish_reason_end_turn() -> None:
    """AnthropicAdapter surfaces end_turn stop_reason."""
    adapter = _anthropic_adapter()
    raw: dict[str, Any] = {
        "id": "msg_2",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": "done"}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    }
    result = adapter.parse_frontier_response(raw)
    assert result["finish_reason"] == "end_turn"


def test_anthropic_parse_mcp_tool_blocks() -> None:
    """Remote MCP tool_use and tool_result blocks surface in tool_calls / server_tools."""
    adapter = _anthropic_adapter()
    raw: dict[str, Any] = {
        "id": "msg_mcp",
        "model": "claude-sonnet-4-6",
        "content": [
            {
                "type": "mcp_tool_use",
                "id": "mcpu_1",
                "name": "agent_bus_read",
                "input": {"tool": "fetch", "arguments": "{}"},
                "server_name": "vortex-code",
            },
            {"type": "mcp_tool_result", "tool_use_id": "mcpu_1", "content": "ok"},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "stop_reason": "end_turn",
    }
    result = adapter.parse_frontier_response(raw)
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "agent_bus_read"
    assert result["tool_calls"][0].get("kind") == "mcp_tool_use"
    assert len(result["server_tool_calls"]) == 2


def test_responses_parse_finish_reason_completed() -> None:
    """ResponsesAPIAdapter surfaces status as finish_reason for completed responses."""
    adapter = _responses_adapter()
    raw: dict[str, Any] = {
        "id": "resp_1",
        "model": "gpt-5.4",
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "hello"}]}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = adapter.parse_frontier_response(raw)
    assert result["finish_reason"] == "completed"


def test_responses_parse_finish_reason_incomplete_max_output_tokens() -> None:
    """ResponsesAPIAdapter derives finish_reason from incomplete_details.reason."""
    adapter = _responses_adapter()
    raw: dict[str, Any] = {
        "id": "resp_2",
        "model": "gpt-5.4",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "partial"}]}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    result = adapter.parse_frontier_response(raw)
    assert result["finish_reason"] == "max_output_tokens"


def test_responses_parse_finish_reason_incomplete_no_details() -> None:
    """When incomplete_details is absent, finish_reason falls back to status."""
    adapter = _responses_adapter()
    raw: dict[str, Any] = {
        "id": "resp_3",
        "model": "gpt-5.4",
        "status": "incomplete",
        "output": [],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    result = adapter.parse_frontier_response(raw)
    assert result["finish_reason"] == "incomplete"


# ---------------------------------------------------------------------------
# CP-002 — Responses API append_tool_round full-output replay
# ---------------------------------------------------------------------------


def test_responses_append_tool_round_replays_message_and_function_call() -> None:
    """message and function_call items are always replayed before tool output."""
    adapter = _responses_adapter()
    body: dict[str, Any] = {
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": "go"}],
    }
    raw_response: dict[str, Any] = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "thinking..."}],
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "cortex",
                "arguments": "{}",
            },
        ]
    }
    tool_results: list[dict[str, Any]] = [
        {"id": "c1", "content": '{"result": "ok"}'},
    ]
    adapter.append_tool_round(body, raw_response, tool_results)
    # input[0] = original user turn
    # input[1] = replayed message item
    # input[2] = replayed function_call item
    # input[3] = function_call_output
    assert len(body["input"]) == 4
    assert body["input"][1]["type"] == "message"
    assert body["input"][2]["type"] == "function_call"
    assert body["input"][3]["type"] == "function_call_output"
    assert body["input"][3]["call_id"] == "c1"


def test_responses_append_tool_round_replays_encrypted_reasoning_in_order() -> None:
    """message + reasoning(encrypted) + function_call all replayed in original order."""
    adapter = _responses_adapter()
    body: dict[str, Any] = {
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": "go"}],
    }
    raw_response: dict[str, Any] = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "text"}]},
            {"type": "reasoning", "encrypted_content": "enc-data-xyz"},
            {
                "type": "function_call",
                "call_id": "c2",
                "name": "cortex",
                "arguments": "{}",
            },
        ]
    }
    tool_results: list[dict[str, Any]] = [
        {"id": "c2", "content": '{"result": "ok"}'},
    ]
    adapter.append_tool_round(body, raw_response, tool_results)
    # input[0]=user, [1]=message, [2]=reasoning(encrypted), [3]=function_call, [4]=function_call_output
    assert len(body["input"]) == 5
    assert body["input"][1]["type"] == "message"
    assert body["input"][2]["type"] == "reasoning"
    assert body["input"][2]["encrypted_content"] == "enc-data-xyz"
    assert body["input"][3]["type"] == "function_call"
    assert body["input"][4]["type"] == "function_call_output"


def test_responses_append_tool_round_skips_bare_reasoning() -> None:
    """A reasoning item without encrypted_content must be skipped (rejected on stateless path)."""
    adapter = _responses_adapter()
    body: dict[str, Any] = {
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": "go"}],
    }
    raw_response: dict[str, Any] = {
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "text"}]},
            {"type": "reasoning"},  # bare — no encrypted_content
            {
                "type": "function_call",
                "call_id": "c3",
                "name": "cortex",
                "arguments": "{}",
            },
        ]
    }
    tool_results: list[dict[str, Any]] = [
        {"id": "c3", "content": '{"result": "ok"}'},
    ]
    adapter.append_tool_round(body, raw_response, tool_results)
    # input[0]=original user, [1]=replayed message, [2]=function_call, [3]=function_call_output
    # (bare reasoning skipped)
    assert len(body["input"]) == 4
    assert body["input"][1]["type"] == "message"
    assert body["input"][2]["type"] == "function_call"
    assert body["input"][3]["type"] == "function_call_output"
    # Confirm bare reasoning was not injected
    assert all(item.get("type") != "reasoning" for item in body["input"])


# ---------------------------------------------------------------------------
# Anthropic streaming — MCP blocks + cache token accumulation
# ---------------------------------------------------------------------------


def test_anthropic_reducer_mcp_tool_use_and_result() -> None:
    from llm_adapters.streaming.anthropic import AnthropicReducer
    from sse.protocols import SSEMessage

    reducer = AnthropicReducer()
    state = reducer.initial_state()

    events = [
        SSEMessage(
            event="message_start",
            data={
                "type": "message_start",
                "message": {
                    "id": "msg_mcp",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 5,
                        "cache_creation_input_tokens": 3,
                        "cache_read_input_tokens": 2,
                    },
                },
            },
        ),
        SSEMessage(
            event="content_block_start",
            data={
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "mcp_tool_use",
                    "id": "mcpu_1",
                    "name": "agent_bus_read",
                    "server_name": "vortex-code",
                    "input": {"tool": "fetch"},
                },
            },
        ),
        SSEMessage(
            event="content_block_stop",
            data={"type": "content_block_stop", "index": 0},
        ),
        SSEMessage(
            event="content_block_start",
            data={
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "mcp_tool_result",
                    "tool_use_id": "mcpu_1",
                    "is_error": False,
                    "content": "thread tip",
                },
            },
        ),
        SSEMessage(
            event="content_block_stop",
            data={"type": "content_block_stop", "index": 1},
        ),
        SSEMessage(
            event="message_delta",
            data={
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {
                    "output_tokens": 4,
                    "cache_read_input_tokens": 1,
                    "cache_creation_input_tokens": 2,
                },
            },
        ),
        SSEMessage(event="message_stop", data={"type": "message_stop"}),
    ]
    for ev in events:
        if reducer.reduce(state, ev):
            break
    terminal = AnthropicReducer.to_terminal_dict(state)
    assert len(terminal["content"]) == 2
    assert terminal["content"][0]["type"] == "mcp_tool_use"
    assert terminal["content"][0]["server_name"] == "vortex-code"
    assert terminal["content"][1]["type"] == "mcp_tool_result"
    assert terminal["content"][1]["tool_use_id"] == "mcpu_1"
    assert terminal["content"][1]["content"] == "thread tip"
    assert terminal["usage"]["cache_read_input_tokens"] == 1
    assert terminal["usage"]["cache_creation_input_tokens"] == 2

    adapter = _anthropic_adapter()
    parsed = adapter.parse_frontier_response(terminal)
    assert parsed["usage"]["cached_tokens"] == 1
    assert parsed["usage"]["cache_write_tokens"] == 2


def test_anthropic_reducer_usage_delta_overwrites_start() -> None:
    from llm_adapters.streaming.anthropic import AnthropicReducer
    from sse.protocols import SSEMessage

    reducer = AnthropicReducer()
    state = reducer.initial_state()
    events = [
        SSEMessage(
            event="message_start",
            data={
                "type": "message_start",
                "message": {
                    "id": "msg_u",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 1000,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 100,
                    },
                },
            },
        ),
        SSEMessage(
            event="message_delta",
            data={
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {
                    "output_tokens": 9,
                    "input_tokens": 1100,
                    "cache_read_input_tokens": 2000,
                    "cache_creation_input_tokens": 248,
                },
            },
        ),
        SSEMessage(event="message_stop", data={"type": "message_stop"}),
    ]
    for ev in events:
        if reducer.reduce(state, ev):
            break
    terminal = AnthropicReducer.to_terminal_dict(state)
    assert terminal["usage"]["input_tokens"] == 1100
    assert terminal["usage"]["cache_read_input_tokens"] == 2000
    assert terminal["usage"]["cache_creation_input_tokens"] == 248
    assert terminal["usage"]["output_tokens"] == 9


def test_anthropic_reducer_mcp_tool_result_list_content() -> None:
    from llm_adapters.streaming.anthropic import AnthropicReducer
    from sse.protocols import SSEMessage

    reducer = AnthropicReducer()
    state = reducer.initial_state()
    list_content = [{"type": "text", "text": "structured result"}]
    events = [
        SSEMessage(
            event="message_start",
            data={
                "type": "message_start",
                "message": {"id": "msg_list", "model": "claude-sonnet-4-6", "usage": {}},
            },
        ),
        SSEMessage(
            event="content_block_start",
            data={
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "mcp_tool_result",
                    "tool_use_id": "mcpu_2",
                    "is_error": False,
                    "content": list_content,
                },
            },
        ),
        SSEMessage(
            event="content_block_stop",
            data={"type": "content_block_stop", "index": 0},
        ),
        SSEMessage(event="message_stop", data={"type": "message_stop"}),
    ]
    for ev in events:
        if reducer.reduce(state, ev):
            break
    terminal = AnthropicReducer.to_terminal_dict(state)
    assert terminal["content"][0]["content"] == list_content
