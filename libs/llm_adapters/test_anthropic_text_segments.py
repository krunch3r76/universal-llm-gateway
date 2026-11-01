"""Regression tests for Anthropic adapter text segmentation around tool blocks."""

from __future__ import annotations

import re
from typing import Any

from llm_adapters.anthropic import AnthropicAdapter
from services.git_integration_worker.master_review_owed import CANONICAL_VERDICT_RE

_MCP_FIXTURE_CONTENT: list[dict[str, Any]] = [
    {
        "type": "text",
        "text": "I'll start by reading the retrieval report and the diff.",
    },
    {
        "type": "mcp_tool_use",
        "id": "t1",
        "name": "fs",
        "input": {},
        "server_name": "x",
    },
    {
        "type": "mcp_tool_result",
        "tool_use_id": "t1",
        "content": [{"type": "text", "text": "ok"}],
    },
    {
        "type": "text",
        "text": (
            "VERDICT: APPROVE "
            "range=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.."
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb "
            "head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n\nbody"
        ),
    },
]


def _adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="k-test")


def _raw_response(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_seg",
        "model": "claude-sonnet-4-6",
        "content": content,
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "stop_reason": "end_turn",
    }


def test_parse_frontier_response_splits_text_at_mcp_blocks() -> None:
    adapter = _adapter()
    content = adapter.parse_frontier_response(_raw_response(_MCP_FIXTURE_CONTENT))[
        "content"
    ]
    assert re.search(r"(?m)^VERDICT: APPROVE", content)
    assert ".VERDICT" not in content
    assert CANONICAL_VERDICT_RE.search(content)


def test_adjacent_text_blocks_join_without_separator() -> None:
    adapter = _adapter()
    raw = _raw_response(
        [
            {"type": "text", "text": "part-a"},
            {"type": "text", "text": "part-b"},
        ]
    )
    assert adapter.parse_frontier_response(raw)["content"] == "part-apart-b"


def test_extract_text_matches_parse_frontier_response_content() -> None:
    adapter = _adapter()
    raw = _raw_response(_MCP_FIXTURE_CONTENT)
    parsed_content = adapter.parse_frontier_response(raw)["content"]
    assert adapter.extract_text(raw) == parsed_content


def test_thinking_between_text_does_not_insert_separator() -> None:
    adapter = _adapter()
    raw = _raw_response(
        [
            {"type": "text", "text": "before"},
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "after"},
        ]
    )
    assert adapter.parse_frontier_response(raw)["content"] == "beforeafter"
    assert adapter.extract_text(raw) == "beforeafter"
