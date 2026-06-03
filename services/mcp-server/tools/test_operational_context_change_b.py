"""Change B — consensus-steelman register reframe in operational context."""

from __future__ import annotations

from tools._oc_surface_templates import render_frontier_reasoning
from tools._operational_context import render_operational_context


def test_change_b_drops_non_negotiable_register_label() -> None:
    rendered = render_operational_context(
        "claude-cursor", family="claude", platform="cursor"
    )
    assert "## Proactive Posture" in rendered
    assert "Non-Negotiable" not in rendered
    assert "Never ask for what's in Cortex" in rendered


def test_change_b_lead_seats_get_consensus_rule_zero() -> None:
    for agent, family, platform in (
        ("claude-web", "claude", "web"),
        ("claude-cursor", "claude", "cursor"),
        ("grok-direct", "grok", "direct"),
    ):
        rendered = render_operational_context(agent, family=family, platform=platform)
        assert "consensus_disposition" in rendered
        assert "0. **Material lead decisions**" in rendered
        assert render_frontier_reasoning(lead_posture=True) in rendered


def test_change_b_non_lead_seats_omit_rule_zero() -> None:
    rendered = render_operational_context("gpt-cursor", family="gpt", platform="cursor")
    assert "0. **Material lead decisions**" not in rendered
    assert render_frontier_reasoning(lead_posture=False) in rendered
    assert "1. **Steelman before critique**" in rendered
