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
    deadlines: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    suspected: list[dict[str, Any]],
    hypothesized: list[dict[str, Any]],
    threads: list[dict[str, Any]],
    unread: list[dict[str, Any]],
    review_total: int,
) -> str:
    """Render boot briefing as Markdown narrative."""
    today = datetime.now(UTC).date()
    parts: list[str] = [f"# Boot Briefing — {today.isoformat()}"]

    parts.append("\n## Deadlines")
    if not deadlines:
        parts.append("No active deadlines.")
    else:
        for d in deadlines:
            dl_date = d.get("deadline_date", "")
            remaining = ""
            if dl_date:
                import logging

                _logger = logging.getLogger(__name__)
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
                    pass
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

    parts.append("\n## Open Investigations")
    if not suspected and not hypothesized:
        parts.append("No open investigations.")
    else:
        investigation_categories = {
            "Suspected": suspected,
            "Hypothesized": hypothesized,
        }
        for label, items in investigation_categories.items():
            if items:
                parts.append(f"\n**{label}** ({len(items)}):")
                for a in items:
                    parts.append(f"- [{a.get('entity_id', '?')}] {a.get('claim', '')}")

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

    parts.append(f"\n## Review Queue\n{review_total} item(s) pending review.")
    return "\n".join(parts)
