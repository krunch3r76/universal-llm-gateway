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
