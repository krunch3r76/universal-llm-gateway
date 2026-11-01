"""Glass Cursor Agents sidebar probe classification (offline)."""

from __future__ import annotations

import pytest

from cursor_bridge.chat_surface import _PROBE_JS, classify_surface

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


def test_probe_js_falls_back_to_composer_editor_agent_id() -> None:
    assert "data-sidebar-file-drop-editor-agent-id" in _PROBE_JS
    assert "promptElEarly" in _PROBE_JS
