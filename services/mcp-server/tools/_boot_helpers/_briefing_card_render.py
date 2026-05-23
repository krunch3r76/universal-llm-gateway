"""Per-section render helpers for the briefing card.

Extracted from _briefing_card.py to keep that module under the 400-line
SLOC limit. Callers import ``_truncate_at_sentence``,
``_filter_recent_self_reflections``, and ``_deadline_line`` from here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

# Reflective Journal / Your Notes preview length. Cap is the hard byte ceiling;
# the truncator prefers the last sentence boundary at-or-before the cap so the
# preview doesn't chop mid-sentence ("If I sit with —" was the canonical bug).
_PREVIEW_MAX_CHARS = 200
# Self-reflection recency cap. Older notes drift out of the boot card — agents
# can re-fetch via /assertions if they're chasing a specific historical claim.
_SELF_REFLECTION_MAX_AGE_DAYS = 14


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """Truncate `text` at the last sentence boundary at-or-before `max_chars`.

    Sentence boundaries: '. ', '! ', '? ', or terminal '.'/'!'/'?' at the cap.
    Falls back to a hard-cut + ellipsis when no boundary is found in the
    second half of the window — short fragments stay intact, long unbroken
    prose still gets a clean visual cutoff.
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    # Search for sentence terminators in the back half of the window so a
    # period in the first 20 chars doesn't truncate aggressively.
    cutoff_floor = max_chars // 2
    best = -1
    for marker in (". ", "! ", "? "):
        idx = window.rfind(marker)
        if idx >= cutoff_floor and idx + len(marker) > best:
            best = idx + len(marker.rstrip())
    if best > 0:
        return text[:best].rstrip()
    return window.rstrip() + "…"


def _filter_recent_self_reflections(
    self_reflections: list[dict[str, Any]],
    now: datetime,
    *,
    max_age_days: int = _SELF_REFLECTION_MAX_AGE_DAYS,
) -> list[dict[str, Any]]:
    """Drop self-reflections older than `max_age_days` based on created_at.

    The fetcher already orders DESC by created_at; this is a recency cap on
    top of the fixed limit (default 5). When the agent has fewer than 5
    recent reflections, the section degrades naturally — no padding with
    stale entries.
    """
    if not self_reflections:
        return []
    threshold = now - timedelta(days=max_age_days)
    fresh: list[dict[str, Any]] = []
    for a in self_reflections:
        created = a.get("created_at") or a.get("observed_at") or ""
        if not created:
            # No timestamp — keep it; better to render than silently drop.
            fresh.append(a)
            continue
        try:
            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except ValueError:
            fresh.append(a)
            continue
        if ts >= threshold:
            fresh.append(a)
    return fresh


def render_views_section(views_data: list[dict[str, Any]]) -> list[str]:
    """Render the Views section lines from materialized subgraph data.

    Structural only: entity_count, edge_count, retrieval hint.  No prose,
    no rendered subgraph content (§C.3 compliance).
    """
    lines: list[str] = ["\n## Views"]
    for v in views_data:
        eid = v.get("entity_id", "?")
        ec = v.get("entity_count", 0)
        eg = v.get("edge_count", 0)
        hint = v.get("retrieval_hint", "")
        lines.append(f"- `{eid}` — {ec} entities, {eg} edges | `{hint}`")
    return lines


def render_async_dispatch_section(dispatches: list[dict[str, Any]]) -> list[str]:
    """Render the In-flight Async Dispatches section lines.

    Structural only: execution_id, pipeline_id, started_at, retrieval hint.
    Appears only when called with a non-empty list (§C.3).
    """
    lines: list[str] = [f"\n## In-flight Async Dispatches ({len(dispatches)})"]
    for d in dispatches:
        eid = d.get("execution_id", "?")
        pid = d.get("pipeline_id", "?")
        started = d.get("started_at", "")[:19]  # trim subseconds
        hint = d.get("retrieval_hint", "")
        lines.append(f"- `{eid}` [{pid}] started {started} | `{hint}`")
    return lines


def render_audit_alerts_section(counters: dict[str, int]) -> list[str]:
    """Render the Critical Alerts section lines from audit counters.

    Severity-ordered counts only; no finding content (§C.6).  Returns an
    empty list when there are no criticals (section omitted when silent).
    """
    criticals = counters.get("criticals", 0)
    if criticals == 0:
        return []
    warnings = counters.get("warnings", 0)
    infos = counters.get("infos", 0)
    return [
        "\n## Critical Alerts",
        f"{criticals} critical, {warnings} warning, {infos} info",
        "→ `cortex(tool='audit')` for finding details",
    ]


_SKILL_CLASS_ORDER = (
    "tool_manual",
    "protocol",
    "matter_playbook",
    "discipline",
)
_EXPOSURE_ORDER = ("primary", "overflow", "private")
_EXPOSURE_HEADERS: dict[str, str] = {
    "primary": "### Tool Manuals — Primary",
    "overflow": "### Tool Manuals — Overflow",
    "private": "### Tool Manuals — Private",
}
_CLASS_HEADERS: dict[str, str] = {
    "protocol": "### Protocols",
    "matter_playbook": "### Matter Playbooks",
    "discipline": "### Disciplines",
}


def _skill_slug(skill: dict[str, Any]) -> str:
    entity_id = skill.get("id") or skill.get("entity_id") or "?"
    return skill.get("name") or str(entity_id).removeprefix("agent_skill:")


def _skill_short(skill: dict[str, Any]) -> str:
    short = skill.get("description_first_sentence")
    if not short:
        full = (skill.get("description") or "").strip()
        short = full.split(". ", 1)[0].rstrip(".")
    return short or ""


def _append_skill_rows(lines: list[str], bucket: list[dict[str, Any]]) -> None:
    for skill in sorted(bucket, key=_skill_slug):
        lines.append(f"- **{_skill_slug(skill)}** — {_skill_short(skill)}")


def render_skills_section(
    skills: list[dict[str, Any]],
    skills_unpartitioned_count: int,
) -> list[str]:
    """Group agent skills by class (and tool_manual exposure) for the boot card."""
    lines: list[str] = [
        "\n## Agent Skills "
        "(read on trigger match — "
        "`fs(sandbox='cortex', op='read', "
        "path='agent-skills/<NAME>.md')`)"
    ]
    by_class: dict[str | None, list[dict[str, Any]]] = {}
    for skill in skills:
        by_class.setdefault(skill.get("skill_class"), []).append(skill)

    no_class = by_class.pop(None, [])

    for skill_class in _SKILL_CLASS_ORDER:
        bucket = by_class.pop(skill_class, None)
        if not bucket:
            continue
        if skill_class == "tool_manual":
            by_exposure: dict[str, list[dict[str, Any]]] = {}
            for skill in bucket:
                tb = skill.get("tool_binding") or {}
                exposure = str(tb.get("exposure", "primary")).lower()
                by_exposure.setdefault(exposure, []).append(skill)
            for exposure in _EXPOSURE_ORDER:
                sub = by_exposure.pop(exposure, None)
                if not sub:
                    continue
                lines.append(_EXPOSURE_HEADERS[exposure])
                _append_skill_rows(lines, sub)
            for exposure in sorted(by_exposure):
                lines.append(f"### Tool Manuals — {exposure.replace('_', ' ').title()}")
                _append_skill_rows(lines, by_exposure[exposure])
        else:
            lines.append(_CLASS_HEADERS[skill_class])
            _append_skill_rows(lines, bucket)

    for skill_class in sorted(by_class):
        lines.append(f"### {skill_class.replace('_', ' ').title()}")
        _append_skill_rows(lines, by_class[skill_class])

    if no_class:
        lines.append("### Other Skills")
        _append_skill_rows(lines, no_class)

    if skills_unpartitioned_count:
        lines.append(
            f"\n> **Skill partition drift**: {skills_unpartitioned_count} "
            f"skill(s) missing `applicable_agents` (default to universal "
            f"via COALESCE). Audit: `scripts/cortex/"
            f"backfill_agent_skill_applicability.py --audit`."
        )
    return lines


def _deadline_line(d: dict[str, Any], today: datetime) -> str:
    """Render a single deadline as a compact markdown line."""
    dl_date = d.get("deadline_date", "")
    remaining = ""
    if dl_date:
        try:
            dl = datetime.strptime(dl_date[:10], "%Y-%m-%d").date()
            delta = (dl - today.date()).days
            if delta >= 0:
                remaining = f" ({delta}d)"
            else:
                remaining = f" (**{abs(delta)}d OVERDUE**)"
        except ValueError:
            pass
    return (
        f"- **{dl_date}**{remaining} — "
        f"{d.get('deadline_name', '')} ({d.get('matter_name', '')})"
    )


__all__ = [
    "_PREVIEW_MAX_CHARS",
    "_deadline_line",
    "_filter_recent_self_reflections",
    "_truncate_at_sentence",
    "render_async_dispatch_section",
    "render_audit_alerts_section",
    "render_skills_section",
    "render_views_section",
]
