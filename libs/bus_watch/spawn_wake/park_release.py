"""A roster play is the explicit release of a hop-budget mission park.

The hop reactor parks a mission once the terminal chain hits the cap. That
stamp then blocks every later conductor admit for the same work key, including
a roster row the liaison already marked play. One admit releases a single
parked row. Stacked historical parks would still refuse, so a play clears
every mission-cap park and sets ``generation_options.hop_park_release``. The
admit releases one more if the reactor stamps a park during the post.
"""

from __future__ import annotations

from typing import Any

_MISSION_CAP = "hop_budget_mission_cap"
_RELEASE_BOUND = 32


def arm_hop_park_release(body: dict[str, Any], work_key: str) -> bool:
    """Set the admit release flag when this work key is mission-cap parked.

    Returns True when the body carries the flag. A pytest process that
    refuses the live ledger leaves the body unchanged.
    """
    key = str(work_key or "").strip()
    if not key:
        return False
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import _connect
        from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
            mission_park_state,
            release_mission_park,
        )
    except ImportError:
        return False
    try:
        conn = _connect()
    except RuntimeError:
        return False
    try:
        state = mission_park_state(conn, work_key=key)
        if state is None or state.reason != _MISSION_CAP:
            return False
        for _ in range(_RELEASE_BOUND):
            current = mission_park_state(conn, work_key=key)
            if current is None or current.reason != _MISSION_CAP:
                break
            release_mission_park(
                conn,
                work_key=key,
                thread_id=current.parked_dispatch_id,
                caller_agent="liaison-ticker",
            )
        else:
            return False
        if mission_park_state(conn, work_key=key) is not None:
            conn.rollback()
            return False
        conn.commit()
    finally:
        conn.close()
    opts = dict(body.get("generation_options") or {})
    opts["hop_park_release"] = True
    body["generation_options"] = opts
    return True
