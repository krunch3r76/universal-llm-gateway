"""Quiet-alarm reason from the alarm body.

The subject is fixed. ``closeout_unharvested`` means the worker finished
and the thread was left active. That lane must not keep the row.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from bus_watch.digest_budget import _bus, _get

_QUIET_REASON_RE = re.compile(r"\breason=([a-z_]+)")


def stamp_quiet_reason(client: httpx.Client, lane: dict[str, Any]) -> None:
    """Set ``quiet_reason`` when the lane's latest turn is a quiet alarm."""
    subject = str(lane.get("last_subject") or "").strip().lower()
    if not subject.startswith("quiet with work in flight"):
        return
    thread_id = str(lane.get("id") or "")
    if not thread_id:
        return
    payload = _get(client, "/turns", thread=thread_id, last=1) or {}
    turns = payload.get("turns") or []
    body = str(turns[0].get("body") or "") if turns else ""
    match = _QUIET_REASON_RE.search(body)
    if match:
        lane["quiet_reason"] = match.group(1)


def close_unharvested_quiet_lanes(
    lanes: list[Any],
    *,
    patch,
) -> list[str]:
    """Close lanes whose worker already finished and whose closeout never landed.

    A delivered closeout already auto-closes the thread. This is the other
    case: the quiet alarm is the last turn, so the bus left the lane active.
    A later admit opens a new lane.
    """
    closed: list[str] = []
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        if str(lane.get("quiet_reason") or "") != "closeout_unharvested":
            continue
        if str(lane.get("status") or "").lower() == "closed":
            continue
        thread_id = str(lane.get("id") or "")
        if not thread_id:
            continue
        if not patch(thread_id):
            continue
        lane["status"] = "closed"
        closed.append(thread_id)
    return closed


def close_unharvested_quiet_lanes_on_bus(lanes: list[Any]) -> list[str]:
    """PATCH ``/threads/{id}/close`` for each unharvested quiet lane."""
    def patch(thread_id: str) -> bool:
        try:
            with _bus() as client:
                response = client.patch(
                    f"/threads/{thread_id}/close",
                    json={
                        "summary": "closeout_unharvested",
                        "mark_all_read": False,
                    },
                )
        except httpx.HTTPError:
            return False
        return response.status_code < 400

    return close_unharvested_quiet_lanes(lanes, patch=patch)
