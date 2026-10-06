"""Tests for shared CDP delivery-prefix peeling."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.sealed_cdp_prefix import peel_delivery_prefix

pytestmark = pytest.mark.offline


def test_peel_consumes_render_cdp_required_authority() -> None:
    from claude_bundles.cowork_skill_delivery import render_cdp_required_authority

    marker = render_cdp_required_authority(["reasoning-posture", "cdp-operator-proxy"])
    body = "TYPE: DIRECTIVE\n"
    assert peel_delivery_prefix(marker + body) == body


def test_peel_consumes_format_cdp_use_the_lines() -> None:
    from claude_bundles.cowork_skill_delivery import format_cdp_use_the_lines

    use_lines = format_cdp_use_the_lines(["reasoning-posture", "consult-posture"])
    body = "# work\n"
    assert peel_delivery_prefix(use_lines + body) == body


def test_peel_consumes_render_skill_induction_lines() -> None:
    from claude_bundles.cowork_skill_delivery import render_skill_induction

    induction = render_skill_induction(["reasoning-posture"]) + "\n"
    body = "# work\n"
    assert peel_delivery_prefix(induction + body) == body


def test_peel_consumes_render_cdp_inline_skills_xml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from claude_bundles.cowork_skill_delivery import (
        InjectedSkillBody,
        render_cdp_inline_skills_xml,
    )

    repo = tmp_path
    skill_path = repo / "skill.md"
    skill_path.write_text("# skill\n", encoding="utf-8")
    bodies = [
        InjectedSkillBody(
            slug="ulg-architecture",
            surface_class="cursor_only",
            path=skill_path,
            body="# ulg-architecture\n",
        )
    ]
    block = render_cdp_inline_skills_xml(bodies, repo_root=repo)
    body = "TYPE: handoff\n"
    assert peel_delivery_prefix(block + body).lstrip() == body


def test_peel_consumes_format_house_read_first_block_before_briefing() -> None:
    from agent_bus_store.house_pools import format_house_read_first_block, parse_pools
    from agent_bus_store.test_house_pools import _MANIFEST_BLOCK

    row = parse_pools(f"# card\n\n{_MANIFEST_BLOCK}\n")["fable"]
    house = format_house_read_first_block(house_id="10223", row=row)
    briefing = "# Hop on agent-bus:10223: you are the operator seat\n\n## Data\n"
    body = "TYPE: CONTINUITY_HANDOFF\n"
    stacked = f"{house}\n\n{briefing}{body}"
    peeled = peel_delivery_prefix(stacked)
    assert peeled.startswith("# Hop on agent-bus:")
    assert body in peeled
