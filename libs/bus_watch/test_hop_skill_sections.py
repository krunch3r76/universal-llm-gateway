"""Hop/resume skill section load plans."""

from __future__ import annotations

import pytest

from bus_watch.hop_skill_sections import build_skill_section_plan, format_skill_load_block

pytestmark = pytest.mark.offline


def test_checkpoint_hop_plan_uses_md_read_not_full_load() -> None:
    block = format_skill_load_block("checkpoint_hop")
    assert "md_read" in block
    assert "checkpoint_hop" in block
    assert "full-file skim" in block.lower() or "not full" in block.lower()


def test_build_plan_skips_already_loaded_sections() -> None:
    plan = build_skill_section_plan(
        "house_resume",
        already_loaded={("ide-continuity", "Registers")},
    )
    slugs_sections = {(row.slug, row.section) for row in plan}
    assert ("ide-continuity", "Registers") not in slugs_sections


def test_house_resume_includes_core_slugs() -> None:
    plan = build_skill_section_plan("house_resume")
    slugs = {row.slug for row in plan}
    assert "reasoning-posture" in slugs or "lean-dispatch" in slugs
