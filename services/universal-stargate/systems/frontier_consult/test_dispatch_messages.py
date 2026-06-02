"""Unit tests for dispatch message ingress helpers."""

from __future__ import annotations

from .dispatch_messages import extract_last_user_message, wire_latest_user_turn


def test_extract_last_user_message_scans_backward() -> None:
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "mid"},
        {"role": "user", "content": "  last  "},
    ]
    assert extract_last_user_message(messages) == "last"


def test_extract_last_user_message_empty_when_no_user() -> None:
    assert extract_last_user_message([{"role": "assistant", "content": "x"}]) == ""


def test_wire_latest_user_turn_single_message() -> None:
    messages = [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "new"},
    ]
    assert wire_latest_user_turn(messages) == [{"role": "user", "content": "new"}]
