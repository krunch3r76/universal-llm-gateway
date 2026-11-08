"""chat_target wake line helpers."""

from __future__ import annotations

from cursor_bridge.chat_target import wake_line, wake_lines


def test_wake_lines_single_group_matches_wake_line() -> None:
    groups = [{"thread_id": "15851", "turn": "1647,1648,1649"}]
    assert wake_lines(groups) == wake_line("15851", "1647,1648,1649")


def test_wake_lines_preserves_caller_order() -> None:
    groups = [
        {"thread_id": "200", "turn": 5},
        {"thread_id": "100", "turn": 10},
    ]
    assert wake_lines(groups) == (
        "BRIDGE_WAKE thread=200 turn=5\nBRIDGE_WAKE thread=100 turn=10"
    )
