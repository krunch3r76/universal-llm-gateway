"""Tests for boot-time agent skill index (manifest-only, bodies on demand)."""

from __future__ import annotations

from tools._boot_helpers._briefing_card_render import render_skills_section
from tools._boot_helpers._skill_bodies import skill_relpath


def test_skill_relpath_uses_name() -> None:
    assert skill_relpath({"name": "boot-execution-discipline"}) == (
        "agent-skills/boot-execution-discipline.md"
    )


def test_render_skills_index_not_full_body() -> None:
    skills = [
        {
            "name": "sample-skill",
            "skill_class": "protocol",
            "description_first_sentence": "Do the thing when asked",
        }
    ]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "## Agent Skills (1 on this seat — manifest only)" in rendered
    assert "md_list" in rendered
    assert "**`sample-skill`** — Do the thing when asked" in rendered
    assert 'op="md_read", path="agent-skills/sample-skill.md"' in rendered
    assert "Do the thing when asked.\n" not in rendered
    assert "# Sample Skill" not in rendered


def test_render_skills_without_trigger() -> None:
    skills = [{"name": "bare-skill", "skill_class": "discipline"}]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "**`bare-skill`** |" in rendered
    assert "agent-skills/bare-skill.md" in rendered
