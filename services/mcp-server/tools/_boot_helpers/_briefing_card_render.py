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
