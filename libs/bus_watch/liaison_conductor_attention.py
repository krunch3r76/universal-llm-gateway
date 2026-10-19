"""Conductor child lane attention filtering for liaison digest."""

from __future__ import annotations

import re
from typing import Any

from bus_watch.spawn_pending import row_is_terminal

_CONDUCTOR_ATTENTION_WAKE_RE = re.compile(
    r"(?i)(?:VERDICT|SCORE_RESURFACE|BLOCKED|ESCALATE|HOLD_MERGE|OPERATOR_GATE|"
    r"\bFAILED\b|UNVERIFIED|escalation|"
    r"\bDONE\b|\bG7\b|LANDED|land_disposition:\s*landed|"
    r"park-harvest escalation|conductor-hop escalation)"
)
_ROUTINE_CONDUCTOR_HOP_RE = re.compile(
    r"(?i)^(?:cursor-sdk generate(?:\s+admitted|\s+—)|"
    r"CHECKPOINT — hop\b|park-harvest wake —|cursor-sdk CLOSEOUT\b)"
)


def apply_projection_holder_ids(
    lanes: list[Any], projections: list[Any] | None
) -> None:
    """Copy a live projection's holder id onto the lane with the same thread id."""
    from bus_watch.quiet_reason import usable_holder_id

    by_thread: dict[str, dict[str, Any]] = {}
    for proj in projections or []:
        if not isinstance(proj, dict):
            continue
        thread_id = str(proj.get("thread_id") or "").strip()
        if thread_id:
            by_thread[thread_id] = proj
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        thread_id = str(lane.get("id") or "").strip()
        proj = by_thread.get(thread_id)
        if not proj:
            continue
        lane["live_projection_thread_id"] = thread_id
        dispatch_id = usable_holder_id(proj.get("dispatch_id") or proj.get("op_id"))
        execution_id = usable_holder_id(proj.get("execution_id"))
        if dispatch_id and not usable_holder_id(lane.get("dispatch_id")):
            lane["dispatch_id"] = dispatch_id
        if execution_id and not usable_holder_id(lane.get("execution_id")):
            lane["execution_id"] = execution_id


def _lane_is_conductor_contract(lane: dict[str, Any]) -> bool:
    contract = str(lane.get("contract") or "").strip().lower()
    if contract == "conductor":
        return True
    for tag in lane.get("tags") or []:
        token = str(tag).strip().lower()
        if token == "contract:conductor":
            return True
        if (
            token.startswith("contract:")
            and token.split(":", 1)[1].strip() == "conductor"
        ):
            return True
    return False


def _conductor_lane_enters_attention(lane: dict[str, Any]) -> bool:
    """Conductor child lanes wake the IDE only on terminal/unharvested/gate signals."""
    if str(lane.get("quiet_reason") or "") == "closeout_unharvested":
        return True
    if row_is_terminal(lane) and int(lane.get("unread") or 0) > 0:
        return True
    subject = str(lane.get("last_subject") or "")
    if _CONDUCTOR_ATTENTION_WAKE_RE.search(subject):
        return True
    if _ROUTINE_CONDUCTOR_HOP_RE.search(subject):
        return False
    return False


def filter_conductor_routine_attention(
    attention: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop unread conductor lanes whose last turn is routine hop telemetry."""
    out: list[dict[str, Any]] = []
    for lane in attention:
        if not isinstance(lane, dict):
            continue
        if not _lane_is_conductor_contract(lane) or _conductor_lane_enters_attention(
            lane
        ):
            out.append(lane)
    return out


__all__ = [
    "apply_projection_holder_ids",
    "filter_conductor_routine_attention",
]
