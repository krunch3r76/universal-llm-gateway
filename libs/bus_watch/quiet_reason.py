"""Quiet-alarm reason from the alarm body.

The subject is fixed. ``closeout_unharvested`` means the worker finished
and the thread was left active. That lane must not keep the row.

``holder_lost`` closes the gap when an admitted conductor hop has no live
GIW holder and no worker closeout.
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
    "http://127.0.0.1:8091/api/v1/integrate/active-work",
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
    """Live holder ids from GIW ``/api/v1/integrate/active-work``. ``None`` if unreachable."""
    try:
        response = httpx.get(_GIW_ACTIVE_WORK, timeout=3.0)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
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
            token = str(op.get("op_id") or op.get("dispatch_id") or "").strip()
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
    for key in ("execution_id", "dispatch_id"):
        token = str(lane.get(key) or "").strip()
        if token:
            return token
    subject = str(lane.get("last_subject") or "")
    match = _UUID_RE.search(subject)
    if match:
        return match.group(0)
    short = re.search(r"generate\s+[—-]\s*([0-9a-f]{8,})", subject, re.I)
    return short.group(1) if short else ""


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
            str(lane.get("lifecycle") or "").lower() == "admitted" or "admitted" in subject_l
        ):
            return True
    return False


def _execution_held(execution_id: str, held_execution_ids: set[str] | frozenset[str]) -> bool:
    token = str(execution_id or "").strip().lower()
    if not token:
        return False
    for held in held_execution_ids:
        other = str(held or "").strip().lower()
        if other == token or other.startswith(token):
            return True
    return False


def _qualifies_holder_lost(
    lane: dict[str, Any], held_execution_ids: set[str] | frozenset[str]
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
) -> list[str]:
    """POST ``holder_lost`` terminal closeout on lanes with no live GIW holder."""
    poster = post or (lambda thread_id, execution_id: _post_holder_lost_turn(thread_id, execution_id))
    held = frozenset(str(x) for x in held_execution_ids)
    written: list[str] = []
    for lane in lanes or []:
        qualified = _qualifies_holder_lost(lane, held)
        if not qualified:
            continue
        thread_id, execution_id = qualified
        if not poster(thread_id, execution_id):
            continue
        written.append(thread_id)
    return written
