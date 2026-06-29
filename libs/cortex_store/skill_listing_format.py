"""Shared render-only concise skill index formatter (F2b-i).

Pure function — no SQL, seat filter, or ranking. Consumes already-projected
manifest or boot item dicts from GET /skills.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_seat.body_injection import web_auto_inject_skill_slugs

from .routes.boot._skill_trigger import canonical_skill_summary

_LOAD_CONTRACT_LINE = (
    "> **Load a body**: `GET /skills/body?id=agent_skill:<slug>&expected_digest=<digest>` "
    "(REST) or `fs(cortex, md_read, agent-skills/<slug>.md)`."
)


def _row_slug(row: Mapping[str, Any]) -> str:
    entity_id = row.get("id") or row.get("entity_id")
    if isinstance(entity_id, str) and entity_id.strip():
        slug = entity_id.strip().removeprefix("agent_skill:")
        if slug:
            return slug
    name = row.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "?"


def _prose_display(row: Mapping[str, Any]) -> str:
    return (
        row.get("description_first_sentence")
        or row.get("trigger_short")
        or row.get("trigger")
        or ""
    ).strip()


def _trigger_display(row: Mapping[str, Any]) -> str:
    return (row.get("trigger_short") or row.get("trigger") or "").strip()


def render_concise_skill_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    include_category: bool = True,
    include_digest: bool = False,
    include_source_hint: bool = True,
    max_trigger_chars: int = 160,
) -> str:
    """Render a concise markdown skill index from projected item rows."""
    lines: list[str] = []
    if title:
        lines.append(title)
        lines.append("")
    lines.append(
        f"Seat-filtered manifest ({len(rows)} skills). Bodies load on demand."
    )
    if include_source_hint:
        lines.append("")
        lines.append(_LOAD_CONTRACT_LINE)
    lines.append("")

    if include_category:
        by_cat: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            cat = str(row.get("skill_category") or "uncategorized")
            by_cat.setdefault(cat, []).append(row)
        for cat in sorted(by_cat):
            lines.append(f"## {cat}")
            lines.append("")
            for row in sorted(by_cat[cat], key=_row_slug):
                _append_row_lines(
                    lines,
                    row,
                    include_digest=include_digest,
                    max_trigger_chars=max_trigger_chars,
                )
    else:
        for row in sorted(rows, key=_row_slug):
            _append_row_lines(
                lines,
                row,
                include_digest=include_digest,
                max_trigger_chars=max_trigger_chars,
            )

    return "\n".join(lines).rstrip() + "\n"


def _append_row_lines(
    lines: list[str],
    row: Mapping[str, Any],
    *,
    include_digest: bool,
    max_trigger_chars: int,
) -> None:
    slug = _row_slug(row)
    name = str(row.get("name") or slug)
    prose = _prose_display(row)
    trigger = _trigger_display(row)
    if max_trigger_chars and len(trigger) > max_trigger_chars:
        trigger = trigger[: max_trigger_chars - 1] + "…"
    lines.append(f"### {slug}")
    if name != slug:
        lines.append(f"- **Name**: {name}")
    if prose:
        lines.append(f"- **Summary**: {prose}")
    if trigger and trigger != prose:
        lines.append(f"- **Trigger**: {trigger}")
    if include_digest:
        digest = row.get("digest")
        if isinstance(digest, str) and digest.strip():
            lines.append(f"- **Digest**: {digest.strip()}")
    lines.append("")


_TIER1_GATE_SLUGS: frozenset[str] = frozenset(
    {
        "lead-seat-boot",
        "dispatch-shape",
        "consult-routing",
        "completion-provenance-discipline",
        "consensus-steelman-posture",
        "lead-agent-git-integration",
        "session-close",
    }
)

def _skill_trigger_display(row: Mapping[str, Any]) -> str:
    """Listing trigger: trigger_short first; short description fallback only."""
    return canonical_skill_summary(
        row.get("trigger_short"),
        str(row.get("description_first_sentence") or ""),
        max_chars=72,
    )


def _skill_tags_suffix(row: Mapping[str, Any], *, max_tags: int = 3) -> str:
    """Net-new tags only: trigger_match_terms minus tokens already in trigger_short."""
    terms = row.get("trigger_match_terms") or []
    if not terms:
        return ""
    shown = {t for t in (row.get("trigger_short") or "").lower().split() if t}
    net_new = [str(t) for t in terms if str(t).lower() not in shown]
    if not net_new:
        return ""
    return f" [{', '.join(net_new[:max_tags])}]"


def _is_gate_skill(row: Mapping[str, Any]) -> bool:
    return (
        row.get("boot_importance") == "required_gate"
        or _row_slug(row) in _TIER1_GATE_SLUGS
    )


def _append_skill_line_flat(
    lines: list[str], row: Mapping[str, Any], *, is_gate: bool
) -> None:
    slug = _row_slug(row)
    marker = "⚑ " if is_gate else ""
    trigger = _skill_trigger_display(row)
    tags = _skill_tags_suffix(row)
    trigger_part = f" — {trigger}" if trigger else ""
    lines.append(f"- {marker}`{slug}`{trigger_part}{tags}")


def render_skills_card_section(
    items: Sequence[Mapping[str, Any]],
    unpartitioned_count: int,
) -> str:
    """Render boot-card Agent Skills block (domain-grouped concise manifest)."""
    gate_ids = {_row_slug(s) for s in items if _is_gate_skill(s)}
    lines: list[str] = [
        f"\n## Agent Skills ({len(items)} active — concise manifest)",
        (
            "> **Load on demand**: "
            '`fs(sandbox="cortex", op="md_read", path="agent-skills/<slug>.md")` '
            "— slug is the backticked id on each line. Web auto-inject bodies "
            f"({', '.join(f'`{slug}`' for slug in web_auto_inject_skill_slugs())}) "
            "append to the web prompt (`seat_preloaded`)."
        ),
        (
            "> **Discovery (you call it, never the operator)**: at task inflection "
            "points call `skill_suggest(conversation_context=…)` BEFORE scanning "
            'this manifest. If unbound: `tool_search("skill suggest skills loaded delta")` '
            "— not the bare tool name (overflow index only)."
        ),
        "> `⚑` = required gate.",
    ]
    by_domain: dict[str, list[Mapping[str, Any]]] = {}
    for row in items:
        by_domain.setdefault(str(row.get("skill_category") or "uncategorized"), []).append(
            row
        )
    for domain in sorted(by_domain):
        bucket = by_domain[domain]
        lines.append(f"\n**{domain} ({len(bucket)})**")
        for row in sorted(bucket, key=_row_slug):
            _append_skill_line_flat(
                lines, row, is_gate=_row_slug(row) in gate_ids
            )

    if unpartitioned_count:
        lines.append(
            f"\n> **Skill partition drift**: {unpartitioned_count} "
            f"skill(s) missing `applicable_agents` — WITHHELD from all seats "
            f"(default-deny); run backfill. Audit: `scripts/cortex/"
            f"backfill_agent_skill_applicability.py --audit`."
        )
    return "\n".join(lines)


__all__ = ["render_concise_skill_index", "render_skills_card_section"]
