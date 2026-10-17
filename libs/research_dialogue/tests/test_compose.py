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
    assert "stream" not in body


def test_default_body_non_stream() -> None:
    body = build_messages_body(
        turn_index=2,
        history=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
        ledger_verbatim="L",
        challenge="c",
        retrieval_results=[],
        dropped_queries=[],
    )
    assert "stream" not in body


def test_retrieval_xml_escapes_attributes() -> None:
    from research_dialogue.retrieval import SearchResult

    body = build_messages_body(
        turn_index=2,
        history=[{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}],
        ledger_verbatim="L",
        challenge="c",
        retrieval_results=[
            SearchResult(
                search_id='id"1',
                scope="<s>",
                query="a&b",
                weak_match=False,
                rerank_status=None,
                top_ce=None,
                passages=[],
            )
        ],
        dropped_queries=[],
    )
    user_text = body["messages"][-1]["content"][0]["text"]
    assert "&lt;s&gt;" in user_text
    assert "&quot;" in user_text or "&#x27;" not in user_text
    assert "a&amp;b" in user_text


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


def test_closing_cache_on_last_history_not_p4_user() -> None:
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "assistant reply"},
    ]
    body = build_messages_body(
        turn_index=3,
        history=history,
        closing=True,
        stop_reason="turn_limit",
    )
    assert body["system"][0].get("cache_control") == {"type": "ephemeral"}
    history_cache = 0
    for msg in body["messages"][:-1]:
        content = msg.get("content")
        if isinstance(content, str):
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    history_cache += 1
                    assert msg is body["messages"][-2]
    assert history_cache == 1
    p4_user = body["messages"][-1]["content"][0]
    assert "cache_control" not in p4_user
