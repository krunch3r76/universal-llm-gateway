"""Relative time formatting utility."""

from __future__ import annotations

from datetime import UTC, datetime


def relative_time(iso_str: str | None, now: datetime) -> str:
    """Format an ISO timestamp as a human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta_s = (now - ts).total_seconds()
        if delta_s < 0:
            return "just now"
        if delta_s < 3600:
            return f"{int(delta_s / 60)}m ago"
        if delta_s < 86400:
            return f"{int(delta_s / 3600)}h ago"
        return f"{int(delta_s / 86400)}d ago"
    except (ValueError, TypeError):
        return "unknown"
