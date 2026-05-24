"""Summary builders for unread threads and review queue top items."""

from __future__ import annotations

from typing import Any

_UNREAD_THREAD_CAP = 10


def build_unread_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract threads with unread counts for the briefing card.

    Capped at _UNREAD_THREAD_CAP — the briefing card surfaces a navigation
    pointer, not the full inbox. Agents pull the rest via the bus manifest hint.
    """
    unread = [
        {
            "id": t.get("id", ""),
            "slug": t.get("slug", ""),
            "unread": t.get("unread_count", 0),
        }
        for t in threads
        if t.get("unread_count", 0) > 0
    ]
    return unread[:_UNREAD_THREAD_CAP]


def build_review_top(staging_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract top staging items for the briefing card."""
    return [
        {
            "id": s.get("id", "?"),
            "name": s.get("name", s.get("entity_id", "?")),
            "reason": s.get("reason", s.get("review_status", "pending")),
        }
        for s in staging_items[:3]
    ]
