from __future__ import annotations

import pytest

from research_dialogue.compose import assert_no_forbidden_keys, build_messages_body

pytestmark = pytest.mark.offline


def test_forbidden_keys_absent() -> None:
    body = build_messages_body(
        turn_index=1,
        history=[],
        excerpt_text="excerpt",
        minor_notes="notes",
        smoke=True,
    )
    assert_no_forbidden_keys(body)
    for key in ("metadata", "tools", "mcp_servers"):
        assert key not in body


def test_smoke_style_user_has_no_cache_control() -> None:
    body = build_messages_body(
        turn_index=1,
        history=[],
        excerpt_text="x",
        minor_notes="y",
        smoke=True,
    )
    user_blocks = body["messages"][-1]["content"]
    assert "cache_control" not in user_blocks[0]


def test_multiturn_cache_breakpoints() -> None:
    history = [
        {"role": "user", "content": "turn1 user"},
        {"role": "assistant", "content": "turn1 assistant"},
    ]
    body = build_messages_body(
        turn_index=2,
        history=history,
        ledger_verbatim="F-a | x",
        challenge="challenge",
        retrieval_results=[],
        dropped_queries=[],
    )
    assert body["system"][0].get("cache_control") == {"type": "ephemeral"}
    cache_hits = 0
    for msg in body["messages"]:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                cache_hits += 1
                assert msg is body["messages"][-1], "only last user block may carry cache_control"
    assert cache_hits == 1
    for msg in body["messages"][:-1]:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                assert "cache_control" not in block
