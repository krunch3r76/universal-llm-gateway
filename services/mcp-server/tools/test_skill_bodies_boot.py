"""Tests for boot-time agent skill index (manifest-only, bodies on demand)."""

from __future__ import annotations

import tools._boot_helpers._briefing_card_render as render_mod
from tools._boot_helpers._briefing_card_render import render_skills_section
from tools._boot_helpers._skill_bodies import skill_relpath, skill_slug


def test_skill_slug_prefers_entity_id_over_display_name() -> None:
    """Slug derives from the entity id (on-disk form), never the display name.

    Regression guard for friction 16958: returning the display ``name`` here
    produced non-resolving ``agent-skills/<Display Name>.md`` paths on the boot
    card and trained agents to write display-name skill-refs that 404 on read
    and fail the handoff arch-skillref validator.
    """
    row = {
        "id": "agent_skill:architecture-invariants",
        "name": "Architecture Invariants — Universal Layer",
    }
    assert skill_slug(row) == "architecture-invariants"
    assert skill_relpath(row) == "agent-skills/architecture-invariants.md"


def test_skill_slug_accepts_entity_id_key() -> None:
    row = {
        "entity_id": "agent_skill:ulg-architecture",
        "name": "ULG Architecture — Layer",
    }
    assert skill_slug(row) == "ulg-architecture"


def test_skill_slug_falls_back_to_name_when_id_absent() -> None:
    # Name is the fallback only — rows without an id still resolve to a slug.
    assert skill_relpath({"name": "boot-execution-discipline"}) == (
        "agent-skills/boot-execution-discipline.md"
    )


def test_render_skills_index_not_full_body() -> None:
    skills = [
        {
            "name": "sample-skill",
            "skill_category": "protocol",
            "description_first_sentence": "Do the thing when asked",
        }
    ]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "## Agent Skills (1 on this seat — manifest only)" in rendered
    assert "Load on demand" in rendered
    assert "agent-skills/<slug>.md" in rendered
    assert "**`sample-skill`** — Do the thing when asked" in rendered
    assert 'op="md_read", path="agent-skills/sample-skill.md"' not in rendered
    assert "Do the thing when asked.\n" not in rendered
    assert "# Sample Skill" not in rendered
    assert "### Catalog" in rendered


def test_render_skills_prefers_trigger_short() -> None:
    skills = [
        {
            "name": "sample-skill",
            "trigger_short": "Short trigger",
            "description_first_sentence": "Longer fallback sentence",
        }
    ]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "**`sample-skill`** — Short trigger" in rendered
    assert "Longer fallback" not in rendered


def test_render_skills_without_trigger() -> None:
    skills = [{"name": "bare-skill", "skill_category": "discipline"}]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "**`bare-skill`**" in rendered
    assert " — " not in rendered.split("**`bare-skill`**")[1].split("\n")[0]


def test_gate_always_inline() -> None:
    skills = [
        {"name": "dispatch-shape", "boot_importance": "required_gate"},
        {"name": "other-skill", "skill_category": "misc"},
    ]
    rendered = "\n".join(render_skills_section(skills, 0))
    assert "### Required gates" in rendered
    assert "**`dispatch-shape`**" in rendered
    assert rendered.index("### Required gates") < rendered.index("### Catalog")


def test_fol_trigger_does_not_break_ranking() -> None:
    skills = [
        {
            "name": "git-skill",
            "trigger_short": "git ∨ integrate ∧ land",
            "description_first_sentence": "fallback",
        },
        {"name": "other-skill", "description_first_sentence": "unrelated"},
    ]
    rendered = "\n".join(
        render_skills_section(skills, 0, boot_signals={"git", "integrate"})
    )
    assert "### Relevant now" in rendered
    assert "**`git-skill`**" in rendered
    assert rendered.index("### Relevant now") < rendered.index("### Catalog")


def test_fol_ranking_uses_match_terms() -> None:
    skills = [
        {
            "name": "git-skill",
            "trigger_short": "∨ ∧ only",
            "trigger_match_terms": ["git_integrate", "git_land"],
        },
        {"name": "other-skill", "description_first_sentence": "unrelated"},
    ]
    rendered = "\n".join(
        render_skills_section(skills, 0, boot_signals={"git_integrate"})
    )
    assert "### Relevant now" in rendered
    assert (
        "**`git-skill`**"
        in rendered.split("### Relevant now")[1].split("### Catalog")[0]
    )


def test_names_only_keeps_all_slugs(monkeypatch) -> None:
    monkeypatch.setattr(render_mod, "_SKILLS_BYTE_BUDGET", 200)
    skills = [
        {
            "name": f"skill-{i}",
            "skill_category": "misc",
            "trigger_short": f"trigger {i}",
        }
        for i in range(20)
    ]
    rendered = "\n".join(render_skills_section(skills, 0))
    for i in range(20):
        assert f"**`skill-{i}`**" in rendered
    catalog = rendered.split("### Catalog", 1)[1]
    assert " — trigger" not in catalog
