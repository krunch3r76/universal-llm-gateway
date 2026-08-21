"""Hop-cadence lane eligibility — operator standing lanes only.

Nested ``sub_mission`` work lanes (child of another ``sub_mission`` lane) must
not enroll hop watches or fire cadence hops. Example: agent-bus:9540 under
operator lane 9534 — both carry ``bus_lifecycle:persistent``, but only the
standing operator lane is a cadence target.
"""

from __future__ import annotations

import sqlite3

from agent_bus_store.db.lane_associations import get_current_lane

SKIP_NESTED_WORK_LANE = "nested_sub_mission_work_lane"


def nested_sub_mission_work_lane(thread_id: str) -> tuple[bool, str | None]:
    """Return ``(True, reason)`` when *thread_id* is a nested work lane.

    A nested work lane is ``lane_role=sub_mission`` whose ``parent_thread`` is
    itself an associated ``sub_mission`` lane (work commissioned under an
    operator sub_mission, not the operator standing lane under a root).
    """
    tid = (thread_id or "").strip()
    if not tid:
        return False, None
    try:
        lane = get_current_lane(thread_id=tid)
    except (LookupError, OSError, sqlite3.OperationalError):
        return False, None
    if lane.get("state") != "associated":
        return False, None
    if lane.get("lane_role") != "sub_mission":
        return False, None
    parent = str(lane.get("parent_thread") or "").strip()
    if not parent:
        return False, None
    try:
        parent_lane = get_current_lane(thread_id=parent)
    except (LookupError, OSError, sqlite3.OperationalError):
        return False, None
    if (
        parent_lane.get("state") == "associated"
        and parent_lane.get("lane_role") == "sub_mission"
    ):
        return True, SKIP_NESTED_WORK_LANE
    return False, None


def hop_cadence_lane_skip_reason(thread_id: str) -> str | None:
    """Skip reason token for evaluate/enroll, or ``None`` when eligible."""
    blocked, reason = nested_sub_mission_work_lane(thread_id)
    return reason if blocked else None
