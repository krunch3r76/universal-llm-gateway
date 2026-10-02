"""Guard: rendered operator-prompt team_dispatch examples match the MCP signature."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.operator_proxy_mission import ensure_operator_proxy_mission_prompt
from claude_bundles.team_dispatch_call_guard import (
    call_bodies,
    call_example_keywords,
    mcp_tool_param_names,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_FRONTIER = _REPO / "services/mcp-server/tools/frontier.py"


def test_rendered_team_dispatch_keywords_match_mcp_signature() -> None:
    rendered = ensure_operator_proxy_mission_prompt("# Mission\n")
    bodies = call_bodies(rendered)
    assert bodies, "rendered operator prompt has no team_dispatch( example"
    allowed = mcp_tool_param_names(_FRONTIER, "team_dispatch")
    assert allowed, "frontier.py team_dispatch signature was empty"
    unknown: list[str] = []
    for body in bodies:
        for name in call_example_keywords(body):
            if name not in allowed:
                unknown.append(name)
    assert not unknown, (
        "team_dispatch example keywords absent from the MCP signature: "
        + ", ".join(unknown)
    )
