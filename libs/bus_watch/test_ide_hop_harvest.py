"""AC-2: harvest_judgment_turns contract — fetch before qualify in hop script."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bus_watch.judgment_rows import harvest_judgment_turns

pytestmark = pytest.mark.offline


def test_harvest_writes_checkpoint_before_mark_read() -> None:
    calls: list[str] = []

    def get_turns(_root: str, **params: object) -> dict:
        calls.append(f"fetch:{params.get('unread', False)}")
        return {
            "turns": [
                {
                    "turn_number": 36,
                    "from": "web-anthropic",
                    "subject": "cdp reply — abc",
                    "read_at": None,
                    "status": "open",
                    "thread": "11738",
                }
            ]
        }

    def post_checkpoint(_root: str, body: str) -> bool:
        calls.append("checkpoint")
        assert "HARVEST judgment" in body
        return True

    def mark_read(_root: str, turn_numbers: list[int]) -> None:
        calls.append("mark_read")

    out = harvest_judgment_turns(
        "11738",
        get_turns=get_turns,
        post_checkpoint=post_checkpoint,
        mark_read=mark_read,
    )
    assert out["ok"] is True
    assert calls.index("checkpoint") < calls.index("mark_read")


def test_harvest_failed_write_skips_mark_read() -> None:
    marked = MagicMock()
    out = harvest_judgment_turns(
        "11738",
        get_turns=lambda *_a, **_k: {
            "turns": [
                {
                    "turn_number": 36,
                    "from": "web-anthropic",
                    "subject": "SCORE_RESURFACE",
                    "read_at": None,
                    "status": "open",
                    "thread": "11738",
                }
            ]
        },
        post_checkpoint=lambda *_a, **_k: False,
        mark_read=marked,
    )
    assert out["ok"] is False
    marked.assert_not_called()
