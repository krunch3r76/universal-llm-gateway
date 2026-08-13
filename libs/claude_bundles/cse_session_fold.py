"""CSR session transition fold — sole sessions.json writer path."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from claude_bundles import cdp_registry_events as events
from claude_bundles.cdp_registry_store import (
    append_session_transition,
    load_sessions,
    ports_lock,
    read_session_transitions,
    write_sessions,
)
from claude_bundles.cse_session_common import (
    DEFAULT_FALLBACK,
    DEFAULT_WAKE,
    OBLIGATION_KIND_STOP_ACK_OWED,
    OBLIGATION_KIND_WAKE_OWED,
    STATUS_ALARMED,
    STATUS_DISCHARGED,
    STATUS_OPEN,
    STOP_ACK_TTL_S,
    WAKE_TTL_S,
    find_session_by_registration,
    find_session_by_thread,
    session_key,
)


def append_session_transition_locked(
    record: dict[str, Any], *, event: Any | None = None
) -> None:
    """Idempotent durable ingress + fold under ports_lock."""
    append_session_transition(record)
    with ports_lock():
        fold_pending_transitions()
    if event is not None:
        events._mirror_to_event_service(event)


def fold_pending_transitions() -> None:
    """Fold all transition log entries into sessions.json (single writer)."""
    sessions = load_sessions()
    applied = _collect_applied_event_ids(sessions)
    changed = False
    for record in read_session_transitions():
        eid = str(record.get("event_id") or "")
        if not eid or eid in applied:
            continue
        sessions, did = _fold_one(record, sessions)
        if did:
            changed = True
            applied.add(eid)
    if changed:
        write_sessions(sessions)


def _collect_applied_event_ids(sessions: dict[str, dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in sessions.values():
        for eid in row.get("_applied_event_ids") or []:
            out.add(str(eid))
    return out


def _fold_one(
    record: dict[str, Any], sessions: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], bool]:
    event = str(record.get("event") or "")
    payload = record.get("payload") or {}
    eid = str(record.get("event_id") or "")
    handlers: dict[str, Callable[..., tuple[dict[str, dict[str, Any]], bool]]] = {
        "cdp.protocol.parked": _fold_protocol_parked,
        "cdp.wake.delivered": _fold_wake_delivered,
        "cdp.wake.alarm_fired": _fold_wake_alarm,
        "cse.wake.posted": _fold_wake_posted,
        "cse.wake.claimed": _fold_wake_claimed,
        "cdp.stop_ack.opened": _fold_stop_ack_opened,
        "cdp.stop_ack.discharged": _fold_stop_ack_discharged,
        "cdp.stop_ack.alarm_fired": _fold_stop_ack_alarm,
        "cdp.seat.superseded": _fold_seat_superseded,
    }
    handler = handlers.get(event)
    if handler is None:
        return sessions, False
    sessions, changed = handler(record, sessions, payload)
    if changed and eid:
        thread = str(payload.get("thread") or "")
        key = session_key(
            registration_id=payload.get("cse_registration_id") or payload.get("registration_id"),
            thread=thread,
        )
        if find_session_by_thread(sessions, thread):
            _, row = find_session_by_thread(sessions, thread)  # type: ignore[misc]
        else:
            row = sessions.get(key) or {}
        applied = list(row.get("_applied_event_ids") or [])
        if eid not in applied:
            applied.append(eid)
            row["_applied_event_ids"] = applied
            if find_session_by_thread(sessions, thread):
                k, _ = find_session_by_thread(sessions, thread)  # type: ignore[misc]
                sessions[k]["_applied_event_ids"] = applied
            elif key in sessions:
                sessions[key]["_applied_event_ids"] = applied
    return sessions, changed


def _fold_protocol_parked(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    if payload.get("skipped"):
        return sessions, True
    thread = str(payload["thread"])
    registration_id = payload.get("cse_registration_id")
    key = session_key(registration_id=registration_id, thread=thread)
    found = find_session_by_thread(sessions, thread)
    if found:
        key, row = found
    else:
        row = sessions.get(key) or {
            "cse_id": registration_id or f"cse-{thread}",
            "ids": {},
            "obligations": [],
        }
    ids = dict(row.get("ids") or {})
    ids["lane_thread"] = thread
    if payload.get("cse_chat_url"):
        ids["chat_url"] = payload["cse_chat_url"]
    if registration_id:
        ids["registration_id"] = registration_id
    row["ids"] = ids
    parked_ts = float(payload.get("parked_ts") or record.get("ts") or time.time())
    obligation_id = str(payload.get("obligation_id") or f"wake:{thread}:{payload['turn_id']}")
    ob = {
        "obligation_id": obligation_id,
        "kind": OBLIGATION_KIND_WAKE_OWED,
        "since": parked_ts,
        "ttl_deadline": parked_ts + WAKE_TTL_S,
        "status": STATUS_OPEN,
        "evidence": [
            {
                "turn_id": payload["turn_id"],
                "thread": thread,
                "turn_number": payload.get("turn_number"),
            }
        ],
        "wake_channel": payload.get("wake_channel") or DEFAULT_WAKE,
        "fallback": payload.get("fallback") or DEFAULT_FALLBACK,
        "cse_chat_url": payload.get("cse_chat_url"),
        "cse_registration_id": registration_id,
        "payment": {"wake_posted": False, "followup_ok": False, "codes": []},
        "alarm": {"fired_at": None, "outcome_code": None},
    }
    obligations = list(row.get("obligations") or [])
    if not any(o.get("obligation_id") == obligation_id for o in obligations):
        obligations.append(ob)
        row["obligations"] = obligations
        row["authorization"] = {"parked_turn": payload["turn_id"]}
        sessions[key] = row
        return sessions, True
    return sessions, False


def _fold_wake_delivered(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    thread = payload.get("thread")
    changed = False
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("obligation_id") != obligation_id and thread:
                ids = row.get("ids") or {}
                if str(ids.get("lane_thread") or "") != str(thread):
                    continue
            if ob.get("obligation_id") == obligation_id or (
                thread
                and str((row.get("ids") or {}).get("lane_thread") or "") == str(thread)
                and ob.get("kind") == OBLIGATION_KIND_WAKE_OWED
                and ob.get("status") in (STATUS_OPEN, STATUS_ALARMED)
            ):
                if ob.get("status") == STATUS_DISCHARGED:
                    return sessions, True
                ob["status"] = STATUS_DISCHARGED
                payment = dict(ob.get("payment") or {})
                payment["followup_ok"] = True
                codes = list(payment.get("codes") or [])
                codes.append("csr.wake.delivered")
                payment["codes"] = codes
                ob["payment"] = payment
                changed = True
        if changed:
            sessions[key] = row
            break
    return sessions, changed


def _fold_wake_alarm(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    fired_at = float(payload.get("fired_at") or record.get("ts") or time.time())
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("obligation_id") == obligation_id:
                if ob.get("status") == STATUS_ALARMED:
                    return sessions, True
                ob["status"] = STATUS_ALARMED
                ob["alarm"] = {
                    "fired_at": fired_at,
                    "outcome_code": payload.get("outcome_code") or "csr.wake.alarm_fired",
                }
                sessions[key] = row
                return sessions, True
    return sessions, False


def _fold_wake_posted(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("obligation_id") == obligation_id:
                payment = dict(ob.get("payment") or {})
                payment["wake_posted"] = True
                ob["payment"] = payment
                sessions[key] = row
                return sessions, True
    return sessions, False


def _fold_wake_claimed(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("obligation_id") != obligation_id:
                continue
            if ob.get("status") not in (STATUS_OPEN, STATUS_ALARMED):
                return sessions, True
            payment = dict(ob.get("payment") or {})
            if payment.get("claimed"):
                return sessions, True
            payment["claimed"] = True
            payment["claimed_at"] = float(record.get("ts") or time.time())
            ob["payment"] = payment
            sessions[key] = row
            return sessions, True
    return sessions, False


def _stop_ack_session_key(payload: dict[str, Any]) -> str:
    registration_id = payload.get("registration_id")
    execution_id = str(payload.get("execution_id") or "")
    if registration_id:
        return session_key(registration_id=str(registration_id), thread=execution_id)
    return f"exec:{execution_id}"


def _fold_stop_ack_opened(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    execution_id = str(payload.get("execution_id") or "")
    registration_id = payload.get("registration_id")
    obligation_id = str(payload.get("obligation_id") or f"stop_ack:{execution_id}")
    key = _stop_ack_session_key(payload)
    found = (
        find_session_by_registration(sessions, str(registration_id))
        if registration_id
        else None
    )
    if found:
        key, row = found
    else:
        row = sessions.get(key) or {
            "cse_id": registration_id or f"cse-{execution_id}",
            "ids": {},
            "obligations": [],
        }
    ids = dict(row.get("ids") or {})
    if registration_id:
        ids["registration_id"] = registration_id
    row["ids"] = ids
    since = float(payload.get("since") or record.get("ts") or time.time())
    ob = {
        "obligation_id": obligation_id,
        "kind": OBLIGATION_KIND_STOP_ACK_OWED,
        "execution_id": execution_id,
        "since": since,
        "ttl_deadline": float(payload.get("ttl_deadline") or since + STOP_ACK_TTL_S),
        "status": STATUS_OPEN,
        "cse_registration_id": registration_id,
        "purpose": payload.get("purpose"),
        "alarm": {"fired_at": None, "ghost_reap_candidate": False},
    }
    obligations = list(row.get("obligations") or [])
    if any(o.get("obligation_id") == obligation_id for o in obligations):
        return sessions, False
    obligations.append(ob)
    row["obligations"] = obligations
    sessions[key] = row
    return sessions, True


def _fold_stop_ack_discharged(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    execution_id = str(payload.get("execution_id") or "")
    changed = False
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_STOP_ACK_OWED:
                continue
            if ob.get("obligation_id") != obligation_id and str(
                ob.get("execution_id") or ""
            ) != execution_id:
                continue
            if ob.get("status") == STATUS_DISCHARGED:
                return sessions, True
            ob["status"] = STATUS_DISCHARGED
            ob["discharge_reason"] = payload.get("reason")
            if payload.get("job"):
                ob["parked_job"] = payload.get("job")
            sessions[key] = row
            return sessions, True
    return sessions, changed


def _fold_stop_ack_alarm(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    obligation_id = str(payload.get("obligation_id") or "")
    fired_at = float(payload.get("fired_at") or record.get("ts") or time.time())
    for key, row in sessions.items():
        for ob in row.get("obligations") or []:
            if ob.get("obligation_id") == obligation_id:
                if ob.get("status") == STATUS_ALARMED:
                    return sessions, True
                ob["status"] = STATUS_ALARMED
                ob["alarm"] = {
                    "fired_at": fired_at,
                    "ghost_reap_candidate": True,
                }
                sessions[key] = row
                return sessions, True
    return sessions, False


def _fold_seat_superseded(
    record: dict[str, Any],
    sessions: dict[str, dict[str, Any]],
    payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Close open wake_owed / stop_ack_owed on the outgoing registration."""
    outgoing = str(payload.get("outgoing_registration_id") or "").strip()
    if not outgoing:
        return sessions, False
    reason = str(payload.get("reason") or "superseded")
    successor = str(payload.get("successor_registration_id") or "").strip() or None
    changed = False
    for key, row in sessions.items():
        ids = row.get("ids") or {}
        row_reg = str(ids.get("registration_id") or "")
        obligations = list(row.get("obligations") or [])
        row_changed = False
        for ob in obligations:
            ob_reg = str(ob.get("cse_registration_id") or "")
            if row_reg != outgoing and ob_reg != outgoing:
                continue
            kind = ob.get("kind")
            if kind not in (OBLIGATION_KIND_WAKE_OWED, OBLIGATION_KIND_STOP_ACK_OWED):
                continue
            if ob.get("status") not in (STATUS_OPEN, STATUS_ALARMED):
                continue
            ob["status"] = STATUS_DISCHARGED
            ob["discharge_reason"] = reason
            if successor:
                ob["successor_registration_id"] = successor
            row_changed = True
        if row_changed:
            row["obligations"] = obligations
            sessions[key] = row
            changed = True
    return sessions, changed
