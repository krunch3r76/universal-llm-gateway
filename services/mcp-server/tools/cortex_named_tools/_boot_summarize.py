"""Summary builders for unread threads and review queue top items."""

from __future__ import annotations

from typing import Any


def _build_unread_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract threads with unread counts for the briefing card."""
    return [
        {
            "id": t.get("id", ""),
            "slug": t.get("slug", ""),
            "unread": t.get("unread_count", 0),
        }
        for t in threads
        if t.get("unread_count", 0) > 0
    ]


def _build_review_top(staging_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract top staging items for the briefing card."""
    return [
        {
            "id": s.get("id", "?"),
            "name": s.get("name", s.get("entity_id", "?")),
            "reason": s.get("reason", s.get("review_status", "pending")),
        }
        for s in staging_items[:3]
    ]
