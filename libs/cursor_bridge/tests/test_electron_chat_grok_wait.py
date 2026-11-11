"""Grok conversation DOM attribution after sidebar select."""

from __future__ import annotations

import pytest
from cursor_bridge.electron_chat_grok_wait import (
    grok_conversation_attributed,
    messages_fingerprint,
)

pytestmark = pytest.mark.offline


def test_empty_conversation_attributed_when_agent_matches() -> None:
    surface = {"active_agent_id": "a1", "recent_messages": []}
    assert grok_conversation_attributed(surface, expected_agent_id="a1", prior_message_fp="old")


def test_stale_messages_not_attributed() -> None:
    fp = messages_fingerprint(
        {"recent_messages": ["stale transcript line from previous chat"]}
    )
    surface = {"active_agent_id": "new-id", "recent_messages": ["stale transcript line from previous chat"]}
    assert grok_conversation_attributed(
        surface, expected_agent_id="new-id", prior_message_fp=fp
    ) is False


def test_fingerprint_change_attributed_with_positive_binding() -> None:
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["fresh line"],
        "has_active_chat": True,
    }
    assert grok_conversation_attributed(
        surface,
        expected_agent_id="new-id",
        prior_message_fp="deadbeef",
        prior_generating=False,
    ) is True


def test_fingerprint_change_without_binding_not_attributed() -> None:
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["fresh line"],
        "has_active_chat": False,
    }
    assert grok_conversation_attributed(
        surface,
        expected_agent_id="new-id",
        prior_message_fp="deadbeef",
        prior_generating=None,
    ) is False


def test_fingerprint_change_while_other_bot_streaming_not_attributed() -> None:
    """Sidebar shows target Y but page prose still grows from bot X."""
    surface = {
        "active_agent_id": "bot-y",
        "recent_messages": ["bot-x streaming grew this line"],
        "has_active_chat": True,
        "generating": True,
    }
    assert grok_conversation_attributed(
        surface,
        expected_agent_id="bot-y",
        prior_message_fp="older-fp",
        prior_generating=True,
    ) is False
