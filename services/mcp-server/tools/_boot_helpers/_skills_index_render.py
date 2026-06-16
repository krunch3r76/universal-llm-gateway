"""Full skills index sidecar + compact web boot skills section (PR1 discovery)."""

from __future__ import annotations

from typing import Any

from agent_seat.body_injection import web_auto_inject_skill_slugs

from ._briefing_card_render import (
    _INVARIANT_SKILL_SLUGS,
    _TIER1_GATE_SLUGS,
    _TIER2_INLINE_MAX,
    _rank_score,
)
from ._skill_bodies import skill_relpath, skill_slug


def skills_index_rel_path(seat_slug: str) -> str:
    return f"notes/system/boot/skills-index-{seat_slug}.md"


def skills_index_cortex_uri(seat_slug: str) -> str:
    return f"cortex:{skills_index_rel_path(seat_slug)}"


def _prose_display(skill: dict[str, Any]) -> str:
    return (
        skill.get("description_first_sentence") or skill.get("trigger_short") or ""
    ).strip()


def _trigger_short_display(skill: dict[str, Any]) -> str:
    return (skill.get("trigger_short") or "").strip()


def _is_gate(skill: dict[str, Any]) -> bool:
    return (
        skill.get("boot_importance") == "required_gate"
        or skill_slug(skill) in _TIER1_GATE_SLUGS
    )


def _category_counts(skills: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for skill in skills:
        cat = skill.get("skill_category") or "uncategorized"
        counts[str(cat)] = counts.get(str(cat), 0) + 1
    return counts


def _source_line(skill: dict[str, Any]) -> str:
    uri = skill.get("source_uri")
    if isinstance(uri, str) and uri.strip():
        return uri.strip()
    return f"cortex:{skill_relpath(skill)}"


def render_skills_index_md(
    seat_slug: str,
    skills: list[dict[str, Any]],
    *,
    preloaded_slugs: tuple[str, ...] | None = None,
) -> str:
    """Full seat-filtered skill index for sidecar persistence."""
    preloaded = set(preloaded_slugs or web_auto_inject_skill_slugs())
    lines: list[str] = [
        f"# Skill index — {seat_slug}",
        "",
        f"Seat-filtered manifest ({len(skills)} skills). Bodies load on demand via "
        "`source_uri` or `fs(cortex, md_read, agent-skills/<slug>.md)`.",
        "",
    ]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for skill in skills:
        cat = str(skill.get("skill_category") or "uncategorized")
        by_cat.setdefault(cat, []).append(skill)
    for cat in sorted(by_cat):
        lines.append(f"## {cat}")
        lines.append("")
        for skill in sorted(by_cat[cat], key=skill_slug):
            slug = skill_slug(skill)
            name = str(skill.get("name") or slug)
            prose = _prose_display(skill)
            trigger = _trigger_short_display(skill)
            pre = " [preloaded]" if slug in preloaded else ""
            lines.append(f"### {slug}{pre}")
            if name != slug:
                lines.append(f"- **Name**: {name}")
            if prose:
                lines.append(f"- **Summary**: {prose}")
            if trigger and trigger != prose:
                lines.append(f"- **Trigger**: {trigger}")
            lines.append(f"- **Source**: {_source_line(skill)}")
            digest = skill.get("digest")
            if isinstance(digest, str) and digest.strip():
                lines.append(f"- **Digest**: {digest.strip()}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_skills_section_compact(
    skills: list[dict[str, Any]],
    skills_unpartitioned_count: int,
    boot_signals: set[str] | None,
    *,
    seat_slug: str,
    index_ref: str | None = None,
    preloaded_slugs: tuple[str, ...] | None = None,
) -> list[str]:
    """Compact web boot skills block: pointer, categories, preload list, ranked preview."""
    ref = index_ref or skills_index_cortex_uri(seat_slug)
    preloaded = list(preloaded_slugs or web_auto_inject_skill_slugs())
    lines: list[str] = [
        f"\n## Agent Skills ({len(skills)} on this seat — compact)",
        f"> **Full index**: `{ref}`",
        (
            "> **Discovery (you call it)**: at task inflection points call "
            "`skill_suggest(loaded=[…], conversation_context=…)` before scanning "
            "the index. Fetch bodies via each row's `source_uri`."
        ),
        (
            "> **Preloaded bodies** (web system prompt + `seat_preloaded`): "
            + ", ".join(f"`{s}`" for s in preloaded)
        ),
    ]
    counts = _category_counts(skills)
    lines.append("\n### Categories")
    for cat in sorted(counts):
        lines.append(f"- **{cat}** — {counts[cat]} skills")

    signals = boot_signals or set()
    tier1 = [s for s in skills if _is_gate(s)]
    rest = [s for s in skills if not _is_gate(s)]
    ranked = sorted(
        ((_rank_score(s, signals), s) for s in rest),
        key=lambda p: (-p[0], skill_slug(p[1])),
    )
    tier2 = [s for score, s in ranked if score > 0][:_TIER2_INLINE_MAX]

    if tier1:
        lines.append("\n### Required gates")
        lines.append(
            "- **skill discovery** — inflection point → `skill_suggest(loaded=…)` first"
        )
        for skill in sorted(tier1, key=skill_slug):
            slug = skill_slug(skill)
            cat = skill.get("skill_category") or "uncategorized"
            prose = _prose_display(skill)
            line = f"- **`{slug}`** · {cat}"
            if prose:
                line += f" · {prose}"
            lines.append(line)

    if tier2:
        lines.append("\n### Relevant now")
        for skill in tier2:
            slug = skill_slug(skill)
            cat = skill.get("skill_category") or "uncategorized"
            prose = _prose_display(skill)
            line = f"- **`{slug}`** · {cat}"
            if prose:
                line += f" · {prose}"
            trigger = _trigger_short_display(skill)
            if trigger and trigger != prose:
                lines.append(line)
                lines.append(f"  Trigger: {trigger}")
            else:
                lines.append(line)

    if skills_unpartitioned_count:
        lines.append(
            f"\n> **Skill partition drift**: {skills_unpartitioned_count} "
            "skill(s) missing `applicable_agents` — withheld from all seats."
        )
    return lines


__all__ = [
    "render_skills_index_md",
    "render_skills_section_compact",
    "skills_index_cortex_uri",
    "skills_index_rel_path",
]
