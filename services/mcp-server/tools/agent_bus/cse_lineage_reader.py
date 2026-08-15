"""Hub-side read-only lane lineage adapter via agent-bus HTTP relay."""

from __future__ import annotations

import time
from typing import Any


class LaneLineageUnreachable(Exception):  # noqa: N818 — dense-spec symbol
    """Relay or hub transport failure while reading lane-current lineage."""


def read_lane_lineage(lane_thread: str) -> dict[str, Any] | None:
    """Fetch derived current parentage for one bus lane thread.

    Returns ``None`` when the lane is unbound (``state=none`` / 404). Raises
    ``LaneLineageUnreachable`` when the relay fails.
    """
    from .lane_associations import _lane_current_impl

    try:
        result = _lane_current_impl(thread_id=lane_thread)
    except Exception as exc:
        raise LaneLineageUnreachable(str(exc)) from exc
    if "error" in result:
        raise LaneLineageUnreachable(str(result["error"]))
    if result.get("state") == "none":
        return None
    observed_at = time.time()
    return {
        "parent_thread": result.get("parent_thread"),
        "lane_role": result.get("lane_role"),
        "association_id": result.get("association_id"),
        "state": result.get("state"),
        "thread_id": result.get("thread_id", lane_thread),
        "lineage_observed_at": observed_at,
        "source": "agent-bus",
    }
