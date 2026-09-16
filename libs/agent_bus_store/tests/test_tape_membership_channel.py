"""Window anchor channel token parsing (_parse_window_lines)."""

from __future__ import annotations

import pytest
from agent_bus_store.tape_membership import _parse_window_lines

pytestmark = pytest.mark.offline


def test_absent_token_is_continuity() -> None:
    body = "Window: transcript_id=550e8400-e29b-41d4-a716-446655440000 · turns@cp=9\n"
    anchors = _parse_window_lines(body)
    assert len(anchors) == 1
    assert anchors[0]["channel"] == "continuity"


def test_hop_token_yields_hop() -> None:
    body = (
        "Window: transcript_id=550e8400-e29b-41d4-a716-446655440000 · "
        "turns@cp=9 · channel=hop\n"
    )
    anchors = _parse_window_lines(body)
    assert anchors[0]["channel"] == "hop"


def test_unknown_token_is_continuity() -> None:
    body = (
        "Window: transcript_id=550e8400-e29b-41d4-a716-446655440000 · "
        "turns@cp=9 · channel=banana\n"
    )
    anchors = _parse_window_lines(body)
    assert len(anchors) == 1
    assert anchors[0]["channel"] == "continuity"


def test_claude_ai_five_token_line_with_hop() -> None:
    body = (
        "Window: chat_url=https://claude.ai/cowork/cse_abc · "
        "transcript_id=550e8400-e29b-41d4-a716-446655440000 · "
        "turns@cp=3 · coverage=tail · channel=hop\n"
    )
    anchors = _parse_window_lines(body)
    assert len(anchors) == 1
    assert anchors[0]["channel"] == "hop"
    assert anchors[0]["turns_at_cp"] == 3
