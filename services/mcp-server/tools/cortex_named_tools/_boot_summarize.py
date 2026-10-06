"""Summary builders for unread threads and review queue top items."""

from __future__ import annotations

from typing import Any

_UNREAD_THREAD_CAP = 10


def build_unread_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract threads with recipient-scoped unread for the briefing card.

    Unread-toc rows (thread/slug/unread_count, no unread_basis) and
    ThreadDetail rows whose unread_basis.recipient is set contribute
    ``unread``. Thread-wide ThreadDetail rows (recipient null) contribute
    ``unstamped`` so the two meanings never share a label.
    """
    out: list[dict[str, Any]] = []
    for t in threads:
        count = t.get("unread_count", t.get("unread", 0)) or 0
        if count <= 0:
            continue
        item: dict[str, Any] = {
            "id": t.get("id") or t.get("thread", ""),
            "slug": t.get("slug", ""),
        }
        basis = t.get("unread_basis")
        if isinstance(basis, dict) and basis.get("recipient") in (None, ""):
            item["unstamped"] = count
        else:
            item["unread"] = count
        out.append(item)
        if len(out) >= _UNREAD_THREAD_CAP:
            break
    return out


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
