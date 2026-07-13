"""Summary builders for unread threads and review queue top items."""

from __future__ import annotations

from typing import Any

_UNREAD_THREAD_CAP = 10


def build_unread_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract threads with unread counts for the briefing card.

    Accepts ThreadDetail rows (id/slug/unread_count) or unread-toc rows
    (thread/slug/unread_count). Capped at _UNREAD_THREAD_CAP.
    """
    unread = [
        {
            "id": t.get("id") or t.get("thread", ""),
            "slug": t.get("slug", ""),
            "unread": t.get("unread_count", t.get("unread", 0)),
        }
        for t in threads
        if (t.get("unread_count", t.get("unread", 0)) or 0) > 0
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
