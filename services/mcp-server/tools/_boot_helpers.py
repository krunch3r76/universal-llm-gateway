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


def render_boot_narrative(
    *,
    deadlines: list[dict[str, Any]] | None = None,
    sessions: list[dict[str, Any]],
    suspected: list[dict[str, Any]] | None = None,
    hypothesized: list[dict[str, Any]] | None = None,
    threads: list[dict[str, Any]],
    unread: list[dict[str, Any]],
    review_total: int | None = None,
    continuation_decisions: list[dict[str, Any]] | None = None,
    continuation_services: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
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
