"""Recipient alias expansion for agent-bus inbox matching."""

from __future__ import annotations

from agent_seat.registry import expand_recipient_slugs, normalize_agent_slug


def test_normalize_web_to_claude_web() -> None:
    assert normalize_agent_slug("web") == "claude-web"


def test_expand_recipient_includes_legacy_web() -> None:
    expanded = expand_recipient_slugs("claude-web")
    assert "web" in expanded
    assert "claude-web" in expanded
