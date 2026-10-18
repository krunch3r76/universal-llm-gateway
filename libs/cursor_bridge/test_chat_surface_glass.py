"""Glass Cursor Agents sidebar probe classification (offline)."""

from __future__ import annotations

import pytest

from cursor_bridge.chat_surface import classify_surface

pytestmark = pytest.mark.offline


def test_classify_log_in_without_rows_is_signed_out() -> None:
    surface = classify_surface(["Log In"], [])
    assert surface["state"] == "signed_out"


def test_classify_glass_sidebar_rows() -> None:
    surface = classify_surface(
        [],
        [
            {
                "text": "18040 lane-x",
                "selected": True,
                "agent_id": "agent-uuid-1",
            },
            {
                "text": "18039 other",
                "selected": False,
                "agent_id": "agent-uuid-2",
            },
        ],
    )
    assert surface["state"] == "chat_list"
    assert surface["active_title"] == "18040 lane-x"
    assert surface["active_agent_id"] == "agent-uuid-1"
