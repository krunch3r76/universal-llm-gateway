"""Tests for cursor_bridge status turn ordering."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.cursor_bridge import _status


def _turn(n: int, subject: str = "MSG") -> dict:
    return {
        "turn_number": n,
        "from": "web-anthropic",
        "to": "cursor",
        "subject": subject,
        "created_at": f"2026-09-10T08:{n:02d}:00Z",
        "body": f"body-{n}",
    }


@pytest.mark.offline
def test_status_last_returns_newest_n_ascending() -> None:
    raw = [_turn(n) for n in range(20, 0, -1)]  # newest-first tip window
    with patch("tools.cursor_bridge._fetch_impl", return_value=raw):
        out = _status(thread="10462", last=5)
    nums = [t["turn_number"] for t in out["turns"]]
    assert nums == [16, 17, 18, 19, 20]


@pytest.mark.offline
def test_status_last_reply_turn_scans_full_window() -> None:
    raw = [_turn(n, "MSG") for n in range(20, 0, -1)]
    raw[3] = {**raw[3], "subject": "REPLY", "from": "cursor"}  # turn 17
    with patch("tools.cursor_bridge._fetch_impl", return_value=raw):
        out = _status(thread="10462", last=3)
    assert out["last_reply_turn"] == 17
    assert [t["turn_number"] for t in out["turns"]] == [18, 19, 20]
