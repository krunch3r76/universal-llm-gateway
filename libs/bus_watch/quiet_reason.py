"""Quiet-alarm reason from the alarm body.

The subject is fixed. ``closeout_unharvested`` means the worker finished
and the thread was left active. That lane must not keep the row.

``holder_lost`` closes the gap when an admitted conductor hop has no live
GIW holder and no worker closeout. A live projection whose ``thread_id``
equals the lane id is that holder. The compared id is a dispatch id or a
full execution id from the lane or that projection. An 8-character subject
prefix is not an id.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

import httpx

from bus_watch.digest_budget import _bus, _get

_GIW_ACTIVE_WORK = os.environ.get(
    "LIAISON_GIW_ACTIVE_WORK",
    "http://127.0.0.1:8091/api/v1/git/active-work",
)
_QUIET_REASON_RE = re.compile(r"\breason=([a-z_]+)")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_TERMINAL_SUBJECT_MARKERS = (
    "CLOSEOUT",
    "Dispatch orphaned",
    "holder_lost",
)
_BARE_PREFIX_RE = re.compile(r"[0-9a-f]{8}", re.I)
# Last successful active-work parse. None means the probe did not succeed.
_LIVE_HOLDER_PROJECTIONS: list[dict[str, Any]] | None = None


def usable_holder_id(token: object) -> str:
    """Dispatch id or full execution id.

    A bare 8-hex token is a subject prefix, not an id that can be compared
    to the held set.
    """
    text = str(token or "").strip()
    if not text or _BARE_PREFIX_RE.fullmatch(text):
        return ""
    return text


def live_holder_projections() -> list[dict[str, Any]] | None:
    """Projections from the last ``fetch_held_execution_ids`` call.

    ``None`` when that probe did not succeed. An empty list means the probe
    succeeded and no live projection was present.
    """
    return _LIVE_HOLDER_PROJECTIONS


def _remember_projections(rows: list[dict[str, Any]] | None) -> None:
    global _LIVE_HOLDER_PROJECTIONS
    _LIVE_HOLDER_PROJECTIONS = rows


def _projections_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Live GIW rows that carry ``thread_id`` and a holder id."""
    out: list[dict[str, Any]] = []
    for op in payload.get("active_ops") or []:
        if not isinstance(op, dict):
            continue
        thread_id = str(op.get("thread_id") or "").strip()
        dispatch_id = usable_holder_id(op.get("dispatch_id") or op.get("op_id"))
        execution_id = usable_holder_id(op.get("execution_id"))
        if not thread_id and not dispatch_id and not execution_id:
            continue
        out.append(
            {
                "thread_id": thread_id,
                "op_id": dispatch_id,
                "dispatch_id": dispatch_id,
                "execution_id": execution_id,
            }
        )
    return out


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


def fetch_held_execution_ids() -> set[str] | None:
    """Live holder ids from GIW ``/api/v1/integrate/active-work``. ``None`` if unreachable.

    Also remembers live projections (``thread_id`` plus dispatch id) for the
    same payload. Those projections are not mixed into the returned id set.
    """
    try:
        response = httpx.get(_GIW_ACTIVE_WORK, timeout=3.0)
    except httpx.HTTPError:
        _remember_projections(None)
        return None
    if response.status_code >= 400:
        _remember_projections(None)
        return None
    try:
        payload = response.json()
    except ValueError:
        _remember_projections(None)
        return None
    if not isinstance(payload, dict):
        _remember_projections(None)
        return None
    _remember_projections(_projections_from_payload(payload))
    held: set[str] = set()
    cursor = payload.get("cursor_dispatches") or {}
    if isinstance(cursor, dict):
        for dispatch_id in cursor.get("dispatch_ids") or []:
            token = str(dispatch_id or "").strip()
            if token:
                held.add(token)
    for gate_key in ("cursor_sdk_gate", "sdk_gate"):
        gate = payload.get(gate_key) or {}
        if not isinstance(gate, dict):
            continue
        holders = gate.get("holders")
        if isinstance(holders, dict):
            for ids in holders.values():
                for holder_id in ids or []:
                    token = str(holder_id or "").strip()
                    if token:
                        held.add(token)
        elif isinstance(holders, list):
            for holder in holders:
                if isinstance(holder, dict):
                    for key in ("execution_id", "dispatch_id", "op_id", "holder_id"):
                        token = str(holder.get(key) or "").strip()
                        if token:
                            held.add(token)
                else:
                    token = str(holder or "").strip()
                    if token:
                        held.add(token)
    for op in payload.get("active_ops") or []:
        if isinstance(op, dict):
            for key in ("op_id", "dispatch_id", "execution_id"):
                token = usable_holder_id(op.get(key))
                if token:
                    held.add(token)
    return held


def _lane_contract(lane: dict[str, Any]) -> str:
    contract = str(lane.get("contract") or "").strip().lower()
    if contract:
        return contract
    for tag in lane.get("tags") or []:
        token = str(tag)
        if token.startswith("contract:"):
            return token.split(":", 1)[1].strip().lower()
    return ""


def _lane_execution_id(lane: dict[str, Any]) -> str:
    """Dispatch id or full execution id on the lane. Never a subject prefix."""
    for key in ("execution_id", "dispatch_id"):
        token = usable_holder_id(lane.get(key))
        if token:
            return token
    subject = str(lane.get("last_subject") or "")
    match = _UUID_RE.search(subject)
    return match.group(0) if match else ""


def _subject_terminal(last_subject: str) -> bool:
    subject = str(last_subject or "")
    upper = subject.upper()
    if "CLOSEOUT" in upper:
        return True
    if "Dispatch orphaned" in subject:
        return True
    if "holder_lost" in subject.lower():
        return True
    return False


def _admitted_cursor_sdk_hop(lane: dict[str, Any]) -> bool:
    subject_l = str(lane.get("last_subject") or "").lower()
    if "cursor-sdk" in subject_l and "admitted" in subject_l:
        return True
    if "generate admitted" in subject_l:
        return True
    contract = _lane_contract(lane)
    lifecycle = str(lane.get("lifecycle") or "").lower()
    if contract == "conductor" and lifecycle in {"admitted", "active"}:
        if "cursor-sdk generate" in subject_l or lifecycle == "admitted":
            return True
    if contract == "conductor" and "admitted" in subject_l:
        return True
    for tag in lane.get("tags") or []:
        token = str(tag).lower()
        if token == "contract:conductor" and (
            str(lane.get("lifecycle") or "").lower() == "admitted"
            or "admitted" in subject_l
        ):
            return True
    return False


def _execution_held(
    execution_id: str, held_execution_ids: set[str] | frozenset[str]
) -> bool:
    token = usable_holder_id(execution_id).lower()
    if not token:
        return False
    for held in held_execution_ids:
        other = usable_holder_id(held).lower()
        if other and other == token:
            return True
    return False


def _projection_for_lane(
    lane: dict[str, Any], projections: list[Any] | None
) -> dict[str, Any] | None:
    """Live projection whose ``thread_id`` equals the lane id."""
    lane_id = str(lane.get("id") or "").strip()
    if not lane_id:
        return None
    for proj in projections or []:
        if not isinstance(proj, dict):
            continue
        if str(proj.get("thread_id") or "").strip() == lane_id:
            return proj
    if str(lane.get("live_projection_thread_id") or "").strip() == lane_id:
        return {"thread_id": lane_id}
    return None


def _qualifies_holder_lost(
    lane: dict[str, Any],
    held_execution_ids: set[str] | frozenset[str],
    projections: list[Any] | None = None,
) -> tuple[str, str] | None:
    if not isinstance(lane, dict):
        return None
    if str(lane.get("status") or "").lower() == "closed":
        return None
    if not _admitted_cursor_sdk_hop(lane):
        return None
    last_subject = str(lane.get("last_subject") or "")
    if _subject_terminal(last_subject):
        return None
    # The projection's thread_id is the lane id. That holder is live even
    # when the subject only carries an execution-id prefix.
    if _projection_for_lane(lane, projections) is not None:
        return None
    execution_id = _lane_execution_id(lane)
    if not execution_id:
        return None
    if _execution_held(execution_id, held_execution_ids):
        return None
    thread_id = str(lane.get("id") or "").strip()
    if not thread_id:
        return None
    return thread_id, execution_id


def _post_holder_lost_turn(thread_id: str, execution_id: str) -> bool:
    payload = {
        "thread": thread_id,
        "from": "liaison-ticker",
        "to": "web-anthropic",
        "subject": f"holder_lost {execution_id}",
        "body": (
            f"execution_id={execution_id}\n"
            "No live holder was found for this admitted hop."
        ),
    }
    try:
        with _bus() as client:
            resp = client.post("/threads/send", json=payload)
    except httpx.HTTPError:
        return False
    return resp.status_code < 400


def reconcile_holder_lost(
    lanes: list[Any],
    held_execution_ids: set[str] | frozenset[str],
    *,
    post: Callable[[str, str], bool] | None = None,
    projections: list[Any] | None = None,
) -> list[str]:
    """POST ``holder_lost`` on lanes with no live GIW holder.

    ``projections`` are live GIW rows (``thread_id``, ``op_id`` /
    ``dispatch_id``, ``execution_id``). A projection whose ``thread_id``
    equals the lane id is a live holder, so nothing is posted. Matching
    rows also copy that dispatch id onto the lane.
    """
    poster = post or (
        lambda thread_id, execution_id: _post_holder_lost_turn(thread_id, execution_id)
    )
    live = [row for row in (projections or []) if isinstance(row, dict)]
    from bus_watch.liaison_digest import apply_projection_holder_ids

    apply_projection_holder_ids(lanes, live)
    held = frozenset(str(x) for x in held_execution_ids)
    written: list[str] = []
    for lane in lanes or []:
        qualified = _qualifies_holder_lost(lane, held, live)
        if not qualified:
            continue
        thread_id, execution_id = qualified
        if not poster(thread_id, execution_id):
            continue
        written.append(thread_id)
    return written
