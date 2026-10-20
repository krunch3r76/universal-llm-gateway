"""Skill SOT: MCP restart hop only when a needed skill chip changed."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_SKILL = (
    Path(__file__).resolve().parents[2]
    / "cursor-plugins/ulg-ecosystem/skills/cdp-operator-proxy/SKILL.md"
)

_CHIP_CHANGED_HOP = (
    "After mcp is healthy, hop only when this leg needs a skill chip that changed"
)


def test_cdp_operator_proxy_mcp_healthy_hop_only_when_skill_chip_changed() -> None:
    """Break: changed chip without hop leaves stale Customize context on the seat."""
    text = _SKILL.read_text(encoding="utf-8")
    assert _CHIP_CHANGED_HOP in text
    assert "3. Hop continuity on the same lane" not in text
    assert "Tooling: healthy → hop" not in text
