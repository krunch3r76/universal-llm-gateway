"""Recipient alias expansion for agent-bus inbox matching."""

from __future__ import annotations

from agent_seat.registry import expand_recipient_slugs, normalize_agent_slug


def test_normalize_web_to_claude_web() -> None:
    assert normalize_agent_slug("web") == "claude-web"


def test_expand_recipient_includes_legacy_web() -> None:
    expanded = expand_recipient_slugs("claude-web")
    assert "web" in expanded
    assert "claude-web" in expanded


def test_persona_aliases_not_normalized() -> None:
    """Legacy persona slugs are retired — use canonical role slugs."""
    assert normalize_agent_slug("oppie") == "oppie"
    assert normalize_agent_slug("orion") == "orion"
    assert normalize_agent_slug("bard") == "bard"
    assert normalize_agent_slug("forge") == "forge"


def test_cursor_orion_not_normalized() -> None:
    """Retired seat alias — use gpt-cursor or gpt_cursor."""
    assert normalize_agent_slug("cursor_orion") == "cursor_orion"
    assert normalize_agent_slug("gpt_cursor") == "gpt-cursor"
