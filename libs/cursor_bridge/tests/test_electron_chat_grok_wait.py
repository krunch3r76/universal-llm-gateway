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


def test_fingerprint_change_attributed() -> None:
    surface = {
        "active_agent_id": "new-id",
        "recent_messages": ["fresh line"],
    }
    assert grok_conversation_attributed(
        surface, expected_agent_id="new-id", prior_message_fp="deadbeef"
    ) is True
