"""Unit tests for advisory Project chrome prompt_template builder."""

from __future__ import annotations

from claude_bundles.project_chrome_prompt import (
    ProjectChromeSpec,
    build_description,
    build_prompt_template,
)


def test_build_prompt_includes_sot_pointers_and_workflow_stub() -> None:
    spec = ProjectChromeSpec(
        name="Prop 19 reinstatement",
        host_id="endeavor:boe19p",
        charter_uri="cortex://notes/system/endeavors/boe19p-charter.md",
        ring_thread="5129",
        scoreboard_uri="cortex://notes/system/threads/4917-charter-scoreboard.md",
    )
    text = build_prompt_template(spec)
    assert "endeavor:boe19p" in text
    assert "cortex://notes/system/endeavors/boe19p-charter.md" in text
    assert "agent-bus:5129" in text
    assert "never gate-bearing" in text
    assert "life MCP workflow dogfood" in text
    assert "Cortex is SoT" in text


def test_custom_workflow_replaces_stub() -> None:
    spec = ProjectChromeSpec(
        name="Life dogfood",
        host_id="endeavor:life-dogfood",
        charter_uri="cortex://x",
        ring_thread="4917",
        workflow_md=(
            "### Agent-bus reply drill\n"
            "1. `agent_bus` fetch thread 4917 unread\n"
            "2. Reply with closeout; leave chrome advisory\n"
        ),
    )
    text = build_prompt_template(spec)
    assert "Agent-bus reply drill" in text
    assert "fetch thread 4917" in text
    assert "Reserved — life MCP" not in text


def test_build_description_default() -> None:
    spec = ProjectChromeSpec(
        name="X",
        host_id="host:a",
        charter_uri="cortex://c",
        ring_thread="1",
    )
    desc = build_description(spec)
    assert "host:a" in desc
    assert "agent-bus:1" in desc
