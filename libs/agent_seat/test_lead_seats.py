"""Lead seat registry — config/agents.yaml lead_seats is the single source."""

from __future__ import annotations

from agent_seat.profiles import load_lead_agent_slugs
from agent_seat.registry import is_lead_agent


def test_load_lead_agent_slugs_from_agents_yaml() -> None:
    slugs = load_lead_agent_slugs()
    assert slugs == frozenset({"claude-web", "claude-cursor", "gpt-cursor"})


def test_is_lead_agent_normalizes_aliases() -> None:
    assert is_lead_agent("claude-web")
    assert is_lead_agent("web")
    assert is_lead_agent("gpt-cursor")
    assert is_lead_agent("gpt_cursor")
    assert not is_lead_agent("gemini-cursor")
