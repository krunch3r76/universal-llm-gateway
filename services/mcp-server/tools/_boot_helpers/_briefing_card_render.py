"""Per-section render helpers for the briefing card.

Extracted from _briefing_card.py to keep that module under the 400-line
SLOC limit. Callers import ``truncate_at_sentence``,
``filter_recent_self_reflections``, and ``deadline_line`` from here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ._skill_bodies import skill_slug
from ._time import relative_time

# Reflective Journal / Your Notes preview length. Cap is the hard byte ceiling;
# the truncator prefers the last sentence boundary at-or-before the cap so the
# preview doesn't chop mid-sentence ("If I sit with —" was the canonical bug).
PREVIEW_MAX_CHARS = 200
# "Your Notes" carries operator directives during their pre-codification window —
# wider preview than the reflective journal, plus an assertion-id recovery handle
# rendered by the caller (thread 1427 F2).
_NOTES_PREVIEW_MAX_CHARS = 320
# Self-reflection recency cap. Older notes drift out of the boot card — agents
# can re-fetch via /assertions if they're chasing a specific historical claim.
_SELF_REFLECTION_MAX_AGE_DAYS = 14


def truncate_at_sentence(text: str, max_chars: int) -> str:
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


def filter_recent_self_reflections(
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


def render_compact_block(
    *,
    principal_name: str,
    durable_identity: str | None,
    active_matters: list[dict[str, Any]],
    today: datetime,
) -> tuple[list[str], set[int]]:
    """Render fields 1+2 at the card head (fact-form, ≤ ~800 bytes target).

    Returns (markdown lines, assertion ids rendered in field 2) for dedup
    against the global ``## Temporally Active`` section (F6).
    """
    if not durable_identity and not active_matters:
        return [], set()

    lines: list[str] = ["\n## Compact"]
    if durable_identity:
        lines.append(f"- **{principal_name}** — {durable_identity}")

    rendered_ids: set[int] = set()
    for row in active_matters[:5]:
        aid = row.get("id")
        if aid is not None:
            rendered_ids.add(int(aid))
        name = row.get("entity_name", row.get("entity_id", "?"))
        until = row.get("valid_until", "")
        tag = ""
        if until:
            try:
                exp = datetime.fromisoformat(str(until).replace("Z", "+00:00")).date()
                delta = (exp - today).days
                if delta == 0:
                    tag = " (expires today)"
                elif delta > 0:
                    tag = f" (expires in {delta}d)"
            except (ValueError, TypeError):
                pass
        claim = str(row.get("claim") or "")[:120]
        lines.append(f"- **{name}**{tag} — {claim}")

    return lines, rendered_ids


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


_INVARIANT_SKILL_SLUGS: frozenset[str] = frozenset(
    {"architecture-invariants", "ulg-architecture"}
)

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
# NOTE: gate slugs are ENTITY ids (what skill_slug() returns), NOT file names —
# verified live: agent_skill:session-close (its file is session-close-kernel.md).
# A2 projection fallback: some entities carry a legacy `trigger_keywords` array.
# COALESCE order for the ranker corpus: trigger_match_terms → trigger_keywords →
# FOL-stripped trigger_short.

_FOL_OPERATORS = {"∨", "∧", "⇒", "⇔", "¬", "→", "∈", "∉", "∪", "∩", "⊆", "⊂", "|"}
_TIER2_INLINE_MAX = 12
_SKILLS_BYTE_BUDGET = 8192


def _normalize_terms(skill: dict[str, Any]) -> set[str]:
    terms = skill.get("trigger_match_terms") or []
    if terms:
        return {t.lower() for t in terms}
    raw = skill.get("trigger_short") or skill.get("description_first_sentence") or ""
    for op in _FOL_OPERATORS:
        raw = raw.replace(op, " ")
    return {tok for tok in raw.lower().split() if len(tok) > 2}


def _rank_score(skill: dict[str, Any], signals: set[str]) -> int:
    return len(_normalize_terms(skill) & signals)


def _skill_trigger_display(skill: dict[str, Any]) -> str:
    return (
        skill.get("trigger_short") or skill.get("description_first_sentence") or ""
    ).strip()


def _append_skill_index(
    lines: list[str], bucket: list[dict[str, Any]], names_only: bool = False
) -> None:
    """Emit `- **slug** — trigger_short` per skill. No per-line fs() call.

    names_only collapses the trigger (Tier-3 under byte pressure) while keeping
    every slug present so no skill becomes undiscoverable.
    """
    for skill in sorted(bucket, key=skill_slug):
        slug = skill_slug(skill)
        if names_only:
            lines.append(f"- **`{slug}`**")
            continue
        trigger = _skill_trigger_display(skill)
        trigger_part = f" — {trigger}" if trigger else ""
        uri = skill.get("source_uri")
        if slug in _INVARIANT_SKILL_SLUGS and isinstance(uri, str) and uri.strip():
            trigger_part = (
                f"{trigger_part} (`{uri.strip()}`)"
                if trigger_part
                else f" (`{uri.strip()}`)"
            )
        lines.append(f"- **`{slug}`**{trigger_part}")


def render_skills_section(
    skills: list[dict[str, Any]],
    skills_unpartitioned_count: int,
    boot_signals: set[str] | None = None,
) -> list[str]:
    """Render skills in 3 tiers: required gates, session-ranked, catalog-by-category."""
    lines: list[str] = [
        f"\n## Agent Skills ({len(skills)} on this seat — manifest only)",
        (
            "> Load on demand: "
            '`fs(sandbox="cortex", op="md_read", path="agent-skills/<slug>.md")` '
            "— slug is the bolded id on each line. "
            "Full index (all triggers): `cortex://agent-skills/README.md`. "
            "Invariant-tier bodies (`architecture-invariants`, `ulg-architecture`) "
            "auto-append to the web system prompt — not inlined here."
        ),
    ]
    signals = boot_signals or set()

    def _is_gate(s: dict[str, Any]) -> bool:
        return (
            s.get("boot_importance") == "required_gate"
            or skill_slug(s) in _TIER1_GATE_SLUGS
        )

    tier1 = [s for s in skills if _is_gate(s)]
    rest = [s for s in skills if not _is_gate(s)]

    ranked = sorted(
        ((_rank_score(s, signals), s) for s in rest),
        key=lambda p: (-p[0], skill_slug(p[1])),
    )
    tier2 = [s for score, s in ranked if score > 0][:_TIER2_INLINE_MAX]
    tier2_ids = {id(s) for s in tier2}
    tier3 = [s for s in rest if id(s) not in tier2_ids]

    if tier1:
        lines.append("\n### Required gates")
        _append_skill_index(lines, tier1)
    if tier2:
        lines.append("\n### Relevant now")
        _append_skill_index(lines, tier2)

    lines.append("\n### Catalog")
    names_only = sum(len(s.encode("utf-8")) for s in lines) >= _SKILLS_BYTE_BUDGET
    by_cat: dict[str | None, list[dict[str, Any]]] = {}
    for s in tier3:
        by_cat.setdefault(s.get("skill_category"), []).append(s)
    for cat in sorted(k for k in by_cat if k is not None):
        lines.append(f"**{cat}** ({len(by_cat[cat])})")
        _append_skill_index(lines, by_cat[cat], names_only=names_only)
        if (
            not names_only
            and sum(len(x.encode("utf-8")) for x in lines) >= _SKILLS_BYTE_BUDGET
        ):
            names_only = True
    uncategorized = by_cat.get(None, [])
    if uncategorized:
        lines.append("**uncategorized**")
        _append_skill_index(lines, uncategorized, names_only=names_only)

    if skills_unpartitioned_count:
        lines.append(
            f"\n> **Skill partition drift**: {skills_unpartitioned_count} "
            f"skill(s) missing `applicable_agents` — WITHHELD from all seats "
            f"(default-deny); run backfill. Audit: `scripts/cortex/"
            f"backfill_agent_skill_applicability.py --audit`."
        )
    return lines


def deadline_line(d: dict[str, Any], today: datetime) -> str:
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


def _short_entity_id(entity_id: str) -> str:
    if ":" in entity_id:
        return entity_id.split(":", 1)[1]
    return entity_id


_ARC_SUMMARY_MAX = 240
_ARC_OPEN_ITEMS_MAX = 2
_ARC_RECOVERY = "cortex(tool='journal_read', arguments='{\"limit\": 1}')"


def render_arc_section(
    *,
    continuity: dict[str, Any] | None,
    last_session: dict[str, Any] | None,
    open_arcs: list[dict[str, Any]] | None,
    in_flight_todos: list[dict[str, Any]] | None,
    deadlines: list[dict[str, Any]] | None,
    now: datetime,
) -> list[str]:
    """Deterministic arc digest — been → are → going (directive 3 / 13717).

    Absorbs the former Last Session + Continuity + Open arcs + Recent Work
    blocks. Inputs are existing render params — zero new fetches. Child todo
    slugs and completed plan phases live in the section manifest, not here.
    """
    if not (continuity or last_session or open_arcs or in_flight_todos or deadlines):
        return []
    lines: list[str] = ["\n## Arc — been → are → going"]

    been_bits: list[str] = []
    chain = (continuity or {}).get("continuity_chain") or []
    if chain:
        tail = " → ".join(chain[-3:]) + " → here"
        continuations = (continuity or {}).get("continuations") or []
        if continuations:
            tail += f" (+{len(continuations)} continuation(s))"
        been_bits.append(tail)
    if last_session:
        rel = relative_time(str(last_session.get("timestamp", "?")), now)
        raw = str(last_session.get("summary", "No summary."))
        cut = truncate_at_sentence(raw, _ARC_SUMMARY_MAX)
        hint = (
            f" [+{len(raw) - len(cut)}ch — `{_ARC_RECOVERY}`]"
            if len(raw) > len(cut)
            else ""
        )
        been_bits.append(
            f"last session ({last_session.get('agent', '?')}, {rel}): {cut}{hint}"
        )
    if been_bits:
        lines.append("**Been**: " + " · ".join(been_bits))

    are_bits: list[str] = []
    for arc in open_arcs or []:
        n = len(arc.get("children") or [])
        are_bits.append(
            f"`{arc.get('id', '?')}` [{arc.get('workflow_state', '?')}]({n})"
        )
    flight = [f"`{t.get('id', '?')}`" for t in in_flight_todos or []]
    if flight:
        are_bits.append("in-flight: " + ", ".join(flight))
    if are_bits:
        lines.append("**Are**: " + " · ".join(are_bits))

    going_bits: list[str] = []
    items = (last_session or {}).get("open_items") or []
    if items:
        shown = "; ".join(
            truncate_at_sentence(str(i), 110) for i in items[:_ARC_OPEN_ITEMS_MAX]
        )
        more = (
            f" (+{len(items) - _ARC_OPEN_ITEMS_MAX} more)"
            if len(items) > _ARC_OPEN_ITEMS_MAX
            else ""
        )
        going_bits.append(f"open items: {shown}{more}")
    else:
        going_bits.append("no carried open items")
    nearest: tuple[int, dict[str, Any]] | None = None
    for d in deadlines or []:
        ds = str(d.get("deadline_date") or "")
        try:
            delta = (datetime.strptime(ds[:10], "%Y-%m-%d").date() - now.date()).days
        except ValueError:
            continue
        if delta >= 0 and (nearest is None or delta < nearest[0]):
            nearest = (delta, d)
    if nearest:
        delta, d = nearest
        going_bits.append(
            f"next deadline: {d.get('deadline_date', '')} ({delta}d) — "
            f"{d.get('deadline_name', '')}"
        )
    lines.append("**Going**: " + " · ".join(going_bits))
    return lines


__all__ = [
    "PREVIEW_MAX_CHARS",
    "deadline_line",
    "filter_recent_self_reflections",
    "truncate_at_sentence",
    "render_arc_section",
    "render_async_dispatch_section",
    "render_audit_alerts_section",
    "render_compact_block",
    "render_skills_section",
    "render_views_section",
]
