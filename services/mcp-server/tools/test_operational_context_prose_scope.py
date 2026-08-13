"""Prose-discipline v1.1 scope is baked into rendered operational context."""

from __future__ import annotations

from tools._oc_knowledge_templates import AGENT_BUS_COMPACT
from tools._operational_context import render_operational_context


def test_prose_discipline_v11_scope_in_operational_context() -> None:
    for family, platform, agent in (
        ("claude", "cursor", "claude-cursor"),
        ("gpt", "cursor", "gpt-cursor"),
        ("grok", "web", "grok-web"),
    ):
        rendered = render_operational_context(agent, family=family, platform=platform)
        assert "## Prose Discipline (v1.1 scope)" in rendered
        assert "Does NOT apply to:" in rendered
        assert "Direct conversational replies to the operator" in rendered
        assert "human reader" not in rendered.lower()
        assert "all conversational prose" not in rendered.lower()


def test_agent_bus_compact_renders_and_teaches_request() -> None:
    rendered = AGENT_BUS_COMPACT.format(agent="claude-web")
    assert 'tool="request"' in rendered
    assert 'tool="hop"' in rendered
    assert 'tool="substrate_graph_write"' in rendered
    assert '"to": "cursor"' in rendered
    assert '"to": "TARGET"' not in rendered
    assert "cursor-auto" in rendered
    assert "never a valid `to`" in rendered
    assert "is **not** a `contract` value" in rendered
