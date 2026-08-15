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
    OBLIGATION_KIND_STOP_ACK_OWED,
    OBLIGATION_KIND_WAKE_OWED,
    STATUS_ALARMED,
    STATUS_OPEN,
    STOP_ACK_TTL_S,
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
    "STOP_ACK_TTL_S",
    "append_session_transition_locked",
    "discharge_stop_ack_owed",
    "emit_wake_delivered_transition",
    "fold_pending_transitions",
    "get_open_stop_ack_owed_for_execution",
    "get_open_wake_owed",
    "maybe_mirror_protocol_turn",
    "mint_stop_ack_owed",
    "record_wake_posted",
    "resolve_payment_channel",
    "resolve_wake_obligation_for_receipt",
    "stamp_session_ids",
    "sweep_stop_ack_owed_ttl",
    "sweep_wake_owed_ttl",
]


def stamp_session_ids(
    *,
    lane_thread: str,
    chat_url: str | None = None,
    registration_id: str | None = None,
) -> None:
    """Join-stamp ids onto session projection when request captures CSE identity.

    ``cse_id`` is the session address (from ``chat_url``), never the registry
    ``registration_id`` (Chrome host bind — arc 6885).
    """
    event_id = f"session.ids_stamped:{lane_thread}:{registration_id or ''}:{chat_url or ''}"
    append_session_transition_locked(
        {
            "event_id": event_id,
            "event": "cse.session.ids_stamped",
            "ts": time.time(),
            "payload": {
                "lane_thread": lane_thread,
                "chat_url": chat_url,
                "registration_id": registration_id,
            },
        }
    )


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


def get_open_stop_ack_owed_for_execution(
    execution_id: str,
) -> dict[str, Any] | None:
    """Return open or alarmed stop_ack_owed for an execution_id."""
    sessions = load_sessions()
    eid = (execution_id or "").strip()
    if not eid:
        return None
    for row in sessions.values():
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_STOP_ACK_OWED:
                continue
            if ob.get("status") not in (STATUS_OPEN, STATUS_ALARMED):
                continue
            if str(ob.get("execution_id") or "") == eid:
                return ob
    return None


def mint_stop_ack_owed(
    *,
    execution_id: str,
    registration_id: str | None,
    purpose: str | None,
    now: float | None = None,
) -> str:
    """Mint stop_ack_owed obligation when stream-stop candidacy is first observed."""
    ts = now if now is not None else time.time()
    obligation_id = f"stop_ack:{execution_id}"
    append_session_transition_locked(
        {
            "event_id": f"stop_ack.opened:{execution_id}",
            "event": "cdp.stop_ack.opened",
            "ts": ts,
            "payload": {
                "execution_id": execution_id,
                "registration_id": registration_id,
                "purpose": purpose,
                "obligation_id": obligation_id,
                "since": ts,
                "ttl_deadline": ts + STOP_ACK_TTL_S,
            },
        }
    )
    return obligation_id


def discharge_stop_ack_owed(
    *,
    execution_id: str,
    reason: str,
    job: str | None = None,
) -> None:
    """Discharge stop_ack_owed after parsed ACK or legitimate park."""
    obligation_id = f"stop_ack:{execution_id}"
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "obligation_id": obligation_id,
        "reason": reason,
    }
    if job is not None:
        payload["job"] = job
    append_session_transition_locked(
        {
            "event_id": f"stop_ack.discharged:{execution_id}",
            "event": "cdp.stop_ack.discharged",
            "ts": time.time(),
            "payload": payload,
        }
    )


def sweep_stop_ack_owed_ttl(
    *,
    now: float | None = None,
    notify_pager: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """TTL alarm sweep for unpaid stop_ack_owed — report-only ghost-reap candidate."""
    with ports_lock():
        fold_pending_transitions()
        sessions = load_sessions()
    ts = now if now is not None else time.time()
    results: list[dict[str, Any]] = []
    for _key, row in list(sessions.items()):
        ids = row.get("ids") or {}
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_STOP_ACK_OWED:
                continue
            if ob.get("status") != STATUS_OPEN:
                continue
            deadline = float(ob.get("ttl_deadline") or 0)
            if ts < deadline:
                continue
            obligation_id = str(ob.get("obligation_id") or "")
            exec_id = str(ob.get("execution_id") or "")
            if notify_pager:
                notify_pager(
                    f"STOP-ACK unpaid TTL execution {exec_id}",
                    f"obligation={obligation_id} ghost_reap_candidate=true",
                )
            fired_at = ts
            append_session_transition_locked(
                {
                    "event_id": f"stop_ack.alarm:{obligation_id}",
                    "event": "cdp.stop_ack.alarm_fired",
                    "ts": fired_at,
                    "payload": {
                        "execution_id": exec_id,
                        "obligation_id": obligation_id,
                        "registration_id": ids.get("registration_id")
                        or ob.get("cse_registration_id"),
                        "fired_at": fired_at,
                        "ghost_reap_candidate": True,
                    },
                }
            )
            results.append(
                {
                    "obligation_id": obligation_id,
                    "execution_id": exec_id,
                    "registration_id": ids.get("registration_id")
                    or ob.get("cse_registration_id"),
                    "fired_at": fired_at,
                    "ghost_reap_candidate": True,
                }
            )
    return results
