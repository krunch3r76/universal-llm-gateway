"""Prefix-stability tests for Responses API append_tool_round."""

from __future__ import annotations

import copy

from llm_adapters.responses import ResponsesAPIAdapter


def test_append_tool_round_preserves_input_prefix() -> None:
    """Prior body[\"input\"] items must remain unchanged after append_tool_round."""
    adapter = ResponsesAPIAdapter(
        api_key="k-test",
        base_url="https://api.openai.com/v1",
        vendor="openai",
    )
    body: dict = {
        "input": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
    }
    prefix_before = copy.deepcopy(body["input"])
    raw_response = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hi there"}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "cortex",
                "arguments": "{}",
            },
        ]
    }
    tool_results = [{"id": "call_1", "content": '{"ok": true}'}]

    adapter.append_tool_round(body, raw_response, tool_results)

    assert body["input"][: len(prefix_before)] == prefix_before
    assert len(body["input"]) > len(prefix_before)
