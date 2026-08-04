"""CSR obligations plane — wake_owed mirror, discharge, TTL sweep (Phase 1)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from claude_bundles import cdp_registry_events as events
from claude_bundles.cdp_registry_store import load_sessions, ports_lock
from claude_bundles.cse_session_common import (
    DEFAULT_FALLBACK,
    DEFAULT_WAKE,
    OBLIGATION_KIND_WAKE_OWED,
    STATUS_ALARMED,
    STATUS_OPEN,
    WAKE_TTL_S,
    find_session_by_registration,
    find_session_by_thread,
    is_parked_body,
    is_registered_lane,
    parse_parked_fields,
)
from claude_bundles.cse_session_fold import (
    append_session_transition_locked,
    fold_pending_transitions,
)

__all__ = [
    "WAKE_TTL_S",
    "append_session_transition_locked",
    "emit_wake_delivered_transition",
    "fold_pending_transitions",
    "get_open_wake_owed",
    "maybe_mirror_protocol_turn",
    "record_wake_posted",
    "resolve_payment_channel",
    "resolve_wake_obligation_for_receipt",
    "stamp_session_ids",
    "sweep_wake_owed_ttl",
]


def stamp_session_ids(
    *,
    lane_thread: str,
    chat_url: str | None = None,
    registration_id: str | None = None,
) -> None:
    """Join-stamp ids onto session projection when request captures CSE identity."""
    from claude_bundles.cdp_registry_store import write_sessions
    from claude_bundles.cse_session_common import session_key

    with ports_lock():
        sessions = load_sessions()
        found = find_session_by_thread(sessions, lane_thread)
        if found is None and registration_id:
            found = find_session_by_registration(sessions, registration_id)
        key, row = found if found else (None, None)
        if row is None:
            cse_id = registration_id or f"cse-{lane_thread}"
            key = session_key(registration_id=registration_id, thread=lane_thread)
            row = {"cse_id": cse_id, "ids": {}, "obligations": []}
            sessions[key] = row
        ids = dict(row.get("ids") or {})
        ids["lane_thread"] = lane_thread
        if chat_url:
            ids["chat_url"] = chat_url
        if registration_id:
            ids["registration_id"] = registration_id
        row["ids"] = ids
        write_sessions(sessions)


def get_open_wake_owed(
    sessions: dict[str, dict[str, Any]], *, thread: str
) -> dict[str, Any] | None:
    """Return open or alarmed wake_owed obligation for a lane thread."""
    found = find_session_by_thread(sessions, thread)
    if found is None:
        return None
    _, row = found
    for ob in row.get("obligations") or []:
        if ob.get("kind") == OBLIGATION_KIND_WAKE_OWED and ob.get("status") == STATUS_OPEN:
            return ob
    for ob in row.get("obligations") or []:
        if ob.get("kind") == OBLIGATION_KIND_WAKE_OWED and ob.get("status") == STATUS_ALARMED:
            return ob
    return None


def resolve_payment_channel(
    sessions: dict[str, dict[str, Any]], *, thread: str
) -> dict[str, str | None]:
    """Payment channel from open wake_owed obligation on the lane."""
    found = find_session_by_thread(sessions, thread)
    if found is None:
        return {"chat_url": None, "registration_id": None}
    _, row = found
    ids = row.get("ids") or {}
    ob = get_open_wake_owed(sessions, thread=thread)
    chat = (ob or {}).get("cse_chat_url") or ids.get("chat_url")
    reg = (ob or {}).get("cse_registration_id") or ids.get("registration_id")
    return {"chat_url": chat, "registration_id": reg}


def record_wake_posted(*, thread: str, obligation_id: str) -> None:
    """Reducer fold: bus WAKE posted — TTL sweep reads payment.wake_posted."""
    append_session_transition_locked(
        {
            "event_id": f"wake.posted:{obligation_id}",
            "event": "cse.wake.posted",
            "ts": time.time(),
            "payload": {"thread": thread, "obligation_id": obligation_id},
        }
    )


def maybe_mirror_protocol_turn(
    *,
    thread: str,
    turn_id: int,
    turn_number: int,
    created_at: str,
    body: str,
) -> None:
    """Post-commit PARKED mirror — registers wake_owed on registered lanes."""
    if not is_parked_body(body):
        return
    fields = parse_parked_fields(body)
    wake_channel = fields.get("wake") or DEFAULT_WAKE
    fallback = fields.get("fallback") or DEFAULT_FALLBACK
    chat_url = fields.get("cse_chat_url")
    registration_id = fields.get("cse_registration_id")

    with ports_lock():
        sessions = load_sessions()
        registered = is_registered_lane(
            sessions, thread=thread, registration_id=registration_id
        )

    event_id = f"protocol.parked:{thread}:{turn_id}"
    payload: dict[str, Any] = {
        "thread": thread,
        "turn_id": turn_id,
        "turn_number": turn_number,
        "created_at": created_at,
        "wake_channel": wake_channel,
        "fallback": fallback,
        "cse_chat_url": chat_url,
        "cse_registration_id": registration_id,
        "skipped": not registered,
        "outcome_code": "csr.wake.not_registered" if not registered else None,
    }
    record = {
        "event_id": event_id,
        "event": "cdp.protocol.parked",
        "ts": time.time(),
        "payload": payload,
    }
    if not registered:
        append_session_transition_locked(record)
        return

    parked_ts = time.time()
    try:
        parked_ts = float(created_at)
    except (TypeError, ValueError):
        pass
    obligation_id = f"wake:{thread}:{turn_id}"
    payload["parked_ts"] = parked_ts
    payload["obligation_id"] = obligation_id
    evt = events.cdp_protocol_parked(
        cse_id=registration_id or f"cse-{thread}",
        registration_id=registration_id,
        thread=thread,
        turn_id=turn_id,
        obligation_id=obligation_id,
        wake_channel=wake_channel,
        fallback=fallback,
    )
    append_session_transition_locked(record, event=evt)


def resolve_wake_obligation_for_receipt(
    registration_id: str,
) -> tuple[str | None, str]:
    """Map followup success to lane thread + obligation_id for discharge."""
    sessions = load_sessions()
    thread: str | None = None
    obligation_id = f"wake:reg:{registration_id}"
    found = find_session_by_registration(sessions, registration_id)
    if found:
        _, row = found
        thread = str((row.get("ids") or {}).get("lane_thread") or "") or None
        if thread:
            ob = get_open_wake_owed(sessions, thread=thread)
            if ob:
                obligation_id = str(ob.get("obligation_id") or obligation_id)
    return thread, obligation_id


def emit_wake_delivered_transition(
    *,
    registration_id: str | None,
    thread: str | None,
    obligation_id: str,
    send_verified: bool,
) -> None:
    """Sole discharge ingress — called from followup success path only."""
    cse_id = registration_id or (f"cse-{thread}" if thread else str(uuid.uuid4()))
    evt = events.cdp_wake_delivered(
        cse_id=cse_id,
        registration_id=registration_id,
        thread=thread,
        obligation_id=obligation_id,
        send_verified=send_verified,
    )
    append_session_transition_locked(
        {
            "event_id": f"wake.delivered:{obligation_id}",
            "event": "cdp.wake.delivered",
            "ts": time.time(),
            "payload": {
                "cse_id": cse_id,
                "registration_id": registration_id,
                "thread": thread,
                "obligation_id": obligation_id,
                "send_verified": send_verified,
            },
        },
        event=evt,
    )


def sweep_wake_owed_ttl(
    *,
    now: float | None = None,
    post_wake: Callable[[str, dict[str, Any]], bool] | None = None,
    notify_pager: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """TTL alarm sweep — fold pending first, then fire fallback for expired obligations."""
    with ports_lock():
        fold_pending_transitions()
        sessions = load_sessions()
    ts = now if now is not None else time.time()
    results: list[dict[str, Any]] = []
    for key, row in list(sessions.items()):
        ids = row.get("ids") or {}
        thread = str(ids.get("lane_thread") or "")
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_WAKE_OWED:
                continue
            if ob.get("status") != STATUS_OPEN:
                continue
            deadline = float(ob.get("ttl_deadline") or 0)
            if ts < deadline:
                continue
            obligation_id = str(ob.get("obligation_id") or "")
            fallback = str(ob.get("fallback") or DEFAULT_FALLBACK)
            payment = ob.get("payment") or {}
            if not payment.get("wake_posted") and post_wake and thread:
                post_wake(thread, ob)
            if notify_pager and "pager" in fallback:
                notify_pager(
                    f"WAKE unpaid TTL thread {thread}",
                    f"obligation={obligation_id} fallback={fallback}",
                )
            fired_at = ts
            evt = events.cdp_wake_alarm_fired(
                cse_id=str(row.get("cse_id") or key),
                registration_id=ids.get("registration_id"),
                thread=thread or None,
                obligation_id=obligation_id,
                fallback=fallback,
                outcome_code="csr.wake.alarm_fired",
            )
            append_session_transition_locked(
                {
                    "event_id": f"wake.alarm:{obligation_id}",
                    "event": "cdp.wake.alarm_fired",
                    "ts": fired_at,
                    "payload": {
                        "thread": thread,
                        "obligation_id": obligation_id,
                        "fallback": fallback,
                        "fired_at": fired_at,
                        "outcome_code": "csr.wake.alarm_fired",
                        "registration_id": ids.get("registration_id"),
                    },
                },
                event=evt,
            )
            results.append(
                {"obligation_id": obligation_id, "thread": thread, "fired_at": fired_at}
            )
    return results
