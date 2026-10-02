"""Roster play — arm hop_park_release when mission-cap budget parks are open."""

from __future__ import annotations

from typing import Any

_MISSION_CAP = "hop_budget_mission_cap"


def arm_hop_park_release(body: dict[str, Any], work_key: str) -> bool:
    """Set the admit release flag when every open budget park is mission-cap.

    Read-only: the admit's ``hop_park_release`` path stamps parks after commit.
    Returns True when the body carries the flag.
    """
    key = str(work_key or "").strip()
    if not key:
        return False
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import _connect
        from services.git_integration_worker.cursor_sdk_conductor_park_gate import (
            open_parks,
        )
    except ImportError:
        return False
    reuse = str(body.get("reuse_thread") or "").strip() or None
    try:
        conn = _connect()
    except RuntimeError:
        return False
    try:
        parks = open_parks(
            conn,
            work_key=key,
            thread_id=reuse,
            kinds=frozenset({"budget"}),
        )
        if not parks:
            return False
        if any(p.reason != _MISSION_CAP for p in parks):
            return False
    finally:
        conn.close()
    opts = dict(body.get("generation_options") or {})
    opts["hop_park_release"] = True
    body["generation_options"] = opts
    return True
