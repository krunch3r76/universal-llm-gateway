"""Glass Cursor Agents sidebar probe classification (offline)."""

from __future__ import annotations

import pytest

from cursor_bridge.chat_surface import (
    _PROBE_JS,
    classify_surface,
    derive_empty_basis,
)

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


def test_probe_js_glass_message_fallback_selectors() -> None:
    assert "data-testid='assistant-message'" in _PROBE_JS
    assert "data-message-author-role" in _PROBE_JS
    assert "empty_basis" in _PROBE_JS
    assert ".composer-human-message" in _PROBE_JS
    assert "agent-transcript-row-markdown" in _PROBE_JS


def test_probe_js_skips_glass_activity_work_rows() -> None:
    assert "agent-transcript-row-activity" in _PROBE_JS
    assert "agent-transcript-row-work-group" in _PROBE_JS


def test_classify_surface_nonempty_messages_sets_messages_present() -> None:
    turns = [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "world"},
    ]
    surface = classify_surface(
        [],
        [{"text": "lane", "selected": True, "agent_id": "a1"}],
        message_turns=turns,
        recent_messages=["hello", "world"],
        has_active_chat=True,
        generating=False,
    )
    assert surface["recent_messages"] == ["hello", "world"]
    assert surface["message_turns"] == turns
    assert surface["empty_basis"] == "messages_present"


def test_classify_active_chat_no_messages_is_extractor_miss() -> None:
    surface = classify_surface(
        [],
        [{"text": "lane", "selected": True, "agent_id": "a1"}],
        message_turns=[],
        recent_messages=[],
        has_active_chat=True,
        generating=False,
    )
    assert surface["empty_basis"] == "extractor_miss"
    assert surface["recent_messages"] == []


def test_derive_empty_basis_pending_while_generating() -> None:
    assert (
        derive_empty_basis(
            message_turns=[],
            recent_messages=[],
            has_active_chat=True,
            generating=True,
        )
        == "pending_no_messages"
    )


def test_derive_empty_basis_no_composer_is_empty_conversation() -> None:
    assert (
        derive_empty_basis(
            message_turns=[],
            recent_messages=[],
            has_active_chat=False,
            generating=False,
        )
        == "empty_conversation"
    )
