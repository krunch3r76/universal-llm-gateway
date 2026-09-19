"""spawn_wake successor context — pointer rows, shared now_row unit."""

from __future__ import annotations

import pytest

from bus_watch.spawn_wake.packet import successor_context_from_digest

pytestmark = pytest.mark.offline


def test_ac5_successor_row_is_pointer_not_summary_prose() -> None:
    digest = {
        "root": {
            "id": "11738",
            "turns": 39,
            "unread_turns": [
                {
                    "turn_number": 36,
                    "from": "web-anthropic",
                    "subject": "cdp reply — abc",
                    "read_at": None,
                    "status": "open",
                    "thread": "11738",
                }
            ],
        },
        "policy": {"now_row": "a:35559 stale"},
        "summary_row": "IN FLIGHT lane prose that must not lead",
    }
    ctx = successor_context_from_digest(digest)
    assert ctx["row_source"] == "judgment"
    assert ctx["row"].startswith("11738#36")
    assert "IN FLIGHT" not in ctx["row"]
    assert "a:35559" not in ctx["row"]
