"""Boot briefing helpers — narrative rendering and response extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def safe_list(raw: dict[str, Any] | list[Any], key: str = "items") -> list[Any]:
    """Extract a list from an API response, returning [] on error."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "error" in raw:
            return []
        return raw.get(key, [])
    return []


def build_gated_entities(
    gated_raw: list[dict[str, Any]],
    temporal_active: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build gated entity entries, tagging source as temporal/gated/both.

    Entities appearing in both temporal surfacing and the journal entity gate
    get tagged 'both' and receive enriched assertion depth in the narrative.
    """
    temporal_entity_ids: set[str] = set()
    for a in temporal_active:
        eid = a.get("entity_id")
        if eid:
            temporal_entity_ids.add(eid)

    result: list[dict[str, Any]] = []
    for entity in gated_raw:
        eid = entity.get("entity_id", "")
        source = "both" if eid in temporal_entity_ids else "gated"
        result.append({**entity, "source": source})
    return result


def render_boot_narrative(
    *,
    boot_sections: dict[str, Any] | None = None,
    deadlines: list[dict[str, Any]] | None = None,
    temporal_active: list[dict[str, Any]] | None = None,
    temporal_upcoming: list[dict[str, Any]] | None = None,
    sessions: list[dict[str, Any]],
    suspected: list[dict[str, Any]] | None = None,
    hypothesized: list[dict[str, Any]] | None = None,
    threads: list[dict[str, Any]],
    unread: list[dict[str, Any]],
    review_total: int | None = None,
    continuation_decisions: list[dict[str, Any]] | None = None,
    continuation_services: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    gated_entities: list[dict[str, Any]] | None = None,
    edges_supersedes: list[dict[str, Any]] | None = None,
    edges_reasoning: list[dict[str, Any]] | None = None,
) -> str:
    """Render boot briefing as Markdown narrative.

    Sections with None values are omitted entirely. This enables
    persona-scoped boot: Cursor skips deadlines, investigations,
    and review queue; Web gets everything.
    """
    import logging

    _logger = logging.getLogger(__name__)
    today = datetime.now(UTC).date()
    parts: list[str] = [f"# Boot Briefing — {today.isoformat()}"]

    if continuation_decisions is not None or continuation_services is not None:
        parts.append("\n## Continuation State")
        has_content = False
        if continuation_decisions:
            has_content = True
            parts.append("\n**Recent decisions:**")
            for a in continuation_decisions:
                eid = a.get("entity_id", "?")
                conf = a.get("confidence", "?")
                parts.append(f"- [{eid}] ({conf}) {a.get('claim', '')}")
        if continuation_services:
            has_content = True
            parts.append("\n**Service observations:**")
            for a in continuation_services:
                eid = a.get("entity_id", "?")
                parts.append(f"- [{eid}] {a.get('claim', '')}")
        if todos:
            has_content = True
            parts.append(f"\n**Open todos** ({len(todos)}):")
            for t in todos:
                parts.append(f"- [{t.get('id', '?')}] {t.get('title', '')}")
        if not has_content:
            parts.append("No continuation state available.")

    if edges_supersedes is not None or edges_reasoning is not None:
        sup = edges_supersedes or []
        reas = edges_reasoning or []
        if sup or reas:
            parts.append("\n## Session Edges (last 48h)")
            if sup:
                parts.append(f"\n**Supersession chains** ({len(sup)}):")
                for e in sup:
                    parts.append(
                        f"- `{e.get('from_node', '?')}` supersedes "
                        f"`{e.get('to_node', '?')}` "
                        f"({e.get('agent', '?')}, {e.get('created_at', '?')[:16]})"
                    )
            if reas:
                parts.append(f"\n**Reasoning edges** ({len(reas)}):")
                for e in reas:
                    ctx = e.get("context", "")
                    ctx_suffix = f" — {ctx}" if ctx else ""
                    parts.append(
                        f"- `{e.get('from_node', '?')}` "
                        f"—[{e.get('edge_type', '?')}]→ "
                        f"`{e.get('to_node', '?')}`{ctx_suffix}"
                    )

    if gated_entities:
        total_assertions = sum(e.get("assertions_shown", 0) for e in gated_entities)
        parts.append(
            f"\n## Gated Entities ({len(gated_entities)} entities, "
            f"{total_assertions} assertions surfaced)"
        )
        for entity in gated_entities:
            eid = entity.get("entity_id", "?")
            name = entity.get("entity_name", eid)
            shown = entity.get("assertions_shown", 0)
            total = entity.get("assertion_count", 0)
            source_tag = entity.get("source", "gated")
            enriched = " [enriched — temporal+gated]" if source_tag == "both" else ""
            parts.append(f"\n### {name} ({shown}/{total} assertions){enriched}")
            for a in entity.get("assertions", []):
                conf = a.get("confidence", "?")
                parts.append(f"- [{conf}] {a.get('claim', '')}")
            if total > shown:
                parts.append(f'-> entity_get("{eid}") for full context')

    if deadlines is not None:
        parts.append("\n## Deadlines")
        if not deadlines:
            parts.append("No active deadlines.")
        else:
            for d in deadlines:
                dl_date = d.get("deadline_date", "")
                remaining = ""
                if dl_date:
                    try:
                        dl = datetime.strptime(dl_date[:10], "%Y-%m-%d").date()
                        delta = (dl - today).days
                        if delta >= 0:
                            remaining = f" ({delta}d)"
                        else:
                            remaining = f" (**{abs(delta)}d OVERDUE**)"
                    except ValueError as e:
                        _logger.warning(
                            "Failed to parse deadline date '%s': %s", dl_date, e
                        )
                parts.append(
                    f"- **{dl_date}**{remaining} — "
                    f"{d.get('deadline_name', '')} ({d.get('matter_name', '')})"
                )

    if temporal_active or temporal_upcoming:
        if temporal_active:
            parts.append("\n## Temporally Active")
            for a in temporal_active:
                name = a.get("entity_name", a.get("entity_id", "?"))
                until = a.get("valid_until", "")
                remaining = ""
                if until:
                    try:
                        exp = datetime.fromisoformat(
                            until.replace("Z", "+00:00")
                        ).date()
                        delta = (exp - today).days
                        if delta == 0:
                            remaining = " (expires today)"
                        elif delta > 0:
                            remaining = f" (expires in {delta}d)"
                        else:
                            remaining = f" (**expired {abs(delta)}d ago**)"
                    except (ValueError, TypeError):
                        pass
                parts.append(f"- **{name}**{remaining} — {a.get('claim', '')}")
        if temporal_upcoming:
            parts.append("\n## Upcoming (next 7 days)")
            for a in temporal_upcoming:
                name = a.get("entity_name", a.get("entity_id", "?"))
                from_date = a.get("valid_from", "")
                starts = ""
                if from_date:
                    try:
                        start = datetime.fromisoformat(
                            from_date.replace("Z", "+00:00")
                        ).date()
                        delta = (start - today).days
                        starts = f" (in {delta}d)" if delta > 0 else " (today)"
                    except (ValueError, TypeError):
                        pass
                parts.append(f"- **{name}**{starts} — {a.get('claim', '')}")

    if boot_sections is not None:
        full = boot_sections.get("full", [])
        oneline = boot_sections.get("oneline", [])
        if full or oneline:
            parts.append("\n## Key Entities")
            for entity in full:
                parts.append(f"\n{entity.get('section_markdown', '')}")
            if oneline:
                parts.append("\n---\n\n### One-Line Summaries")
                for entity in oneline:
                    parts.append(f"- {entity.get('summary', '')}")

    parts.append("\n## Recent Sessions")
    if not sessions:
        parts.append("No recent sessions.")
    else:
        for s in sessions:
            parts.append(f"\n### {s.get('timestamp', '?')} ({s.get('agent', '?')})")
            parts.append(s.get("summary", "No summary."))
            for label, field in [
                ("Decisions", "decisions"),
                ("Open items", "open_items"),
            ]:
                val = s.get(field)
                if val:
                    items = list(val)
                    parts.append(f"**{label}**: {', '.join(str(i) for i in items)}")

    if suspected is not None or hypothesized is not None:
        parts.append("\n## Open Investigations")
        s_list = suspected or []
        h_list = hypothesized or []
        if not s_list and not h_list:
            parts.append("No open investigations.")
        else:
            for label, items in [("Suspected", s_list), ("Hypothesized", h_list)]:
                if items:
                    parts.append(f"\n**{label}** ({len(items)}):")
                    for a in items:
                        parts.append(
                            f"- [{a.get('entity_id', '?')}] {a.get('claim', '')}"
                        )

    parts.append("\n## Agent Bus")
    if not threads:
        parts.append("No active threads.")
    else:
        parts.append(f"{len(threads)} active thread(s):")
        for t in threads:
            unread_ct = t.get("unread_count", 0)
            badge = f" **({unread_ct} unread)**" if unread_ct else ""
            parts.append(f"- #{t.get('id', '?')} {t.get('slug', '')}{badge}")
    if unread:
        parts.append(f"\n{len(unread)} unread turn(s) awaiting attention.")

    if review_total is not None:
        parts.append(f"\n## Review Queue\n{review_total} item(s) pending review.")

    return "\n".join(parts)
