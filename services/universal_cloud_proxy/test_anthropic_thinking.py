"""B2b regression tests — Anthropic thinking-block surfacing.

Covers the adapter-layer extension in ``anthropic_response.py`` that
attaches ``thinking`` content blocks to the translated OpenAI-shape
message as ``reasoning_content``.
"""

from __future__ import annotations

from services.universal_cloud_proxy.adapters.anthropic_response import (
    convert_response_content,
)


def test_no_thinking_block_omits_reasoning_field() -> None:
    content = [{"type": "text", "text": "Hello"}]
    message, finish, citations, mcp_meta = convert_response_content(content)
    assert "reasoning_content" not in message
    assert message["content"] == "Hello"
    assert finish is None
    assert citations == []
    assert mcp_meta == {}


def test_single_thinking_block_surfaces_reasoning_content() -> None:
    content = [
        {"type": "thinking", "thinking": "Let me consider…", "signature": "abc"},
        {"type": "text", "text": "The answer is 42."},
    ]
    message, _, _, _ = convert_response_content(content)
    assert message["content"] == "The answer is 42."
    assert message["reasoning_content"] == [
        {"type": "thinking", "thinking": "Let me consider…", "signature": "abc"},
    ]


def test_multiple_thinking_blocks_preserved_in_order() -> None:
    content = [
        {"type": "thinking", "thinking": "First I", "signature": "s1"},
        {"type": "text", "text": "Partial answer."},
        {"type": "thinking", "thinking": "Reconsidering…", "signature": "s2"},
        {"type": "text", "text": "Final answer."},
    ]
    message, _, _, _ = convert_response_content(content)
    assert message["content"] == "Partial answer.Final answer."
    assert [b["thinking"] for b in message["reasoning_content"]] == [
        "First I",
        "Reconsidering…",
    ]
    assert [b["signature"] for b in message["reasoning_content"]] == ["s1", "s2"]


def test_empty_thinking_text_is_skipped() -> None:
    content = [
        {"type": "thinking", "thinking": "", "signature": "abc"},
        {"type": "text", "text": "Answer."},
    ]
    message, _, _, _ = convert_response_content(content)
    assert "reasoning_content" not in message
    assert message["content"] == "Answer."


def test_redacted_thinking_preserved_as_marker() -> None:
    content = [
        {"type": "redacted_thinking", "data": "<opaque>"},
        {"type": "text", "text": "Output."},
    ]
    message, _, _, _ = convert_response_content(content)
    assert message["reasoning_content"] == [{"type": "redacted_thinking"}]


def test_thinking_block_without_signature_still_surfaces() -> None:
    content = [
        {"type": "thinking", "thinking": "Reasoning."},
        {"type": "text", "text": "Answer."},
    ]
    message, _, _, _ = convert_response_content(content)
    assert message["reasoning_content"] == [
        {"type": "thinking", "thinking": "Reasoning.", "signature": None},
    ]


def test_thinking_coexists_with_tool_use() -> None:
    content = [
        {"type": "thinking", "thinking": "Need to call a tool.", "signature": "s"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "search",
            "input": {"q": "x"},
        },
    ]
    message, finish, _, _ = convert_response_content(content)
    assert finish == "tool_calls"
    assert message["reasoning_content"][0]["thinking"] == "Need to call a tool."
    assert message["tool_calls"][0]["function"]["name"] == "search"
