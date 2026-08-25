"""Tests for GIW dispatch association topic extraction."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_association import (
    extract_dispatch_topic,
)


def test_extract_dispatch_topic_prefers_so_what() -> None:
    body = (
        "TYPE: DIRECTIVE\n"
        "so_what: ULG gains reliable closeout SMS\n"
        "intent: implement the board topic line\n"
    )
    assert extract_dispatch_topic(body) == "ULG gains reliable closeout SMS"


def test_extract_dispatch_topic_prefers_ulg_gain() -> None:
    body = "hello world\nulg_gain: operators see mission prose on the board\n"
    assert extract_dispatch_topic(body) == "operators see mission prose on the board"


def test_extract_dispatch_topic_falls_back_to_first_line() -> None:
    assert extract_dispatch_topic("\n\nFirst non-empty\nsecond\n") == "First non-empty"


def test_extract_dispatch_topic_caps_at_160() -> None:
    long = "word " * 80
    topic = extract_dispatch_topic(long)
    assert topic is not None
    assert len(topic) <= 161
    assert topic.endswith("…")
