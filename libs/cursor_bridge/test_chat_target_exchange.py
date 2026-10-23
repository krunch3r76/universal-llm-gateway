"""Offline tests for stage-manager exchange paste helpers in chat_target."""

from __future__ import annotations

import pytest

from cursor_bridge.chat_target import exchange_line, exchange_lines

pytestmark = pytest.mark.offline


def test_exchange_line_contains_sender_and_body_not_bridge_wake() -> None:
    out = exchange_line("cursor-sdk", "cursor-sdk", "hello stage")
    assert "from: cursor-sdk" in out
    assert "hello stage" in out
    assert not out.startswith("BRIDGE_WAKE")


def test_exchange_line_empty_sender_raises() -> None:
    with pytest.raises(ValueError):
        exchange_line("", "cursor-sdk", "body")


def test_exchange_lines_empty_when_row_lacks_body() -> None:
    rows = [
        {"turn_number": 1, "body": "ok", "from": "a"},
        {"turn_number": 2, "body": "", "from": "b"},
    ]
    assert exchange_lines(rows) == ""
