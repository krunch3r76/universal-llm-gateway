"""Producer-link projection for the agent-bus wait endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

ProducerState = Literal["unknown", "unlinked", "in_flight", "terminal"]

_SOURCE = "thread_dispatch_links"
_RECENT_TERMINAL_WINDOW = timedelta(hours=24)


def classify_producer_link(
    *,
    execution_id: str | None,
    dispatch_links: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify dispatch-link liveness for one pinned execution.

    Always returns the advisory ``producer`` container with keys:
    ``execution_id``, ``pipeline_id``, ``state``, ``terminal_status``,
    ``linked_at``, ``delivery_at``, ``source``.
    """
    if not execution_id:
        return {
            "execution_id": None,
            "pipeline_id": None,
            "state": "unknown",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
        }

    row = next(
        (link for link in dispatch_links if link.get("execution_id") == execution_id),
        None,
    )
    if row is None:
        return {
            "execution_id": execution_id,
            "pipeline_id": None,
            "state": "unlinked",
            "terminal_status": None,
            "linked_at": None,
            "delivery_at": None,
            "source": _SOURCE,
        }

    terminal_status = row.get("terminal_status")
    state: ProducerState = "terminal" if terminal_status else "in_flight"
    out: dict[str, Any] = {
        "execution_id": execution_id,
        "pipeline_id": row.get("pipeline_id"),
        "state": state,
        "terminal_status": terminal_status,
        "linked_at": row.get("linked_at"),
        "delivery_at": row.get("delivery_at"),
        "source": _SOURCE,
    }
    archive_uri = row.get("archive_uri")
    if archive_uri:
        out["archive_uri"] = archive_uri
    return out


def _parse_link_timestamp(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _terminal_link_within_recent_window(
    link: dict[str, Any], *, now: datetime
) -> bool:
    cutoff = now - _RECENT_TERMINAL_WINDOW
    for key in ("delivery_at", "linked_at"):
        ts = _parse_link_timestamp(link.get(key))
        if ts is not None and ts >= cutoff:
            return True
    return False


def project_thread_producers(
    dispatch_links: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Classify dispatch links for the additive ``producers`` wait field.

    In-flight links (``terminal_status IS NULL``) are listed first in
    ``linked_at`` order, then terminal links whose ``delivery_at`` or
    ``linked_at`` falls within the last 24 hours.
    """
    if not dispatch_links:
        return []
    clock = now or datetime.now(UTC)
    in_flight: list[dict[str, Any]] = []
    terminal_recent: list[dict[str, Any]] = []
    for link in dispatch_links:
        if link.get("terminal_status") is None:
            in_flight.append(link)
        elif _terminal_link_within_recent_window(link, now=clock):
            terminal_recent.append(link)
    ordered = in_flight + terminal_recent
    return [
        classify_producer_link(
            execution_id=str(link.get("execution_id") or ""),
            dispatch_links=dispatch_links,
        )
        for link in ordered
        if link.get("execution_id")
    ]
