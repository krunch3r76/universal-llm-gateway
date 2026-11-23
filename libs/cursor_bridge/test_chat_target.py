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


def test_wake_line_mailbox_and_guarded_from() -> None:
    assert (
        wake_line("25022", 3, mailbox="cursor-wake-slug", from_sender="cursor-sdk")
        == "BRIDGE_WAKE thread=25022 turn=3 to=cursor-wake-slug from=cursor-sdk"
    )
    bad = wake_line(
        "25022",
        3,
        mailbox="cursor-wake-slug",
        from_sender="bad sender!@#",
    )
    assert bad.endswith(" from=unknown")
    assert "to=cursor-wake-slug" in bad


def test_wake_line_rejects_bad_mailbox() -> None:
    try:
        wake_line("1", 1, mailbox="NOT VALID")
    except ValueError:
        return
    raise AssertionError("expected ValueError for invalid mailbox")