"""Wake-debt lane retain — harvest/release gated on CSR wake_owed (ep22)."""

from __future__ import annotations

import time
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cdp_registry_store import load_sessions
from claude_bundles.cse_session_common import (
    OBLIGATION_KIND_STOP_ACK_OWED,
    OBLIGATION_KIND_WAKE_OWED,
    STATUS_ALARMED,
    STATUS_OPEN,
    find_session_by_registration,
)
from claude_bundles.cse_session_fold import (
    append_session_transition_locked,
    fold_pending_transitions,
)
from claude_bundles.cse_session_obligations import get_open_wake_owed

__all__ = [
    "get_open_wake_owed_for_registration",
    "registration_has_wake_debt",
    "release_lane_if_debt_cleared",
    "try_claim_wake_payment",
]


def get_open_wake_owed_for_registration(
    sessions: dict[str, dict[str, Any]], registration_id: str
) -> dict[str, Any] | None:
    """Return open/alarmed wake_owed for a CDP registration_id."""
    reg = (registration_id or "").strip()
    if not reg:
        return None
    found = find_session_by_registration(sessions, reg)
    if found:
        _, row = found
        thread = str((row.get("ids") or {}).get("lane_thread") or "")
        if thread:
            ob = get_open_wake_owed(sessions, thread=thread)
            if ob:
                return ob
    for row in sessions.values():
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_WAKE_OWED:
                continue
            if ob.get("status") not in (STATUS_OPEN, STATUS_ALARMED):
                continue
            if str(ob.get("cse_registration_id") or "") == reg:
                return ob
    return None


def _registration_has_open_stop_ack_debt(
    sessions: dict[str, dict[str, Any]], registration_id: str
) -> bool:
    """True when CSR shows open/alarmed stop_ack_owed tied to *registration_id*."""
    reg = (registration_id or "").strip()
    if not reg:
        return False
    for row in sessions.values():
        ids = row.get("ids") or {}
        if str(ids.get("registration_id") or "") == reg:
            for ob in row.get("obligations") or []:
                if ob.get("kind") != OBLIGATION_KIND_STOP_ACK_OWED:
                    continue
                if ob.get("status") in (STATUS_OPEN, STATUS_ALARMED):
                    return True
        for ob in row.get("obligations") or []:
            if ob.get("kind") != OBLIGATION_KIND_STOP_ACK_OWED:
                continue
            if ob.get("status") not in (STATUS_OPEN, STATUS_ALARMED):
                continue
            if str(ob.get("cse_registration_id") or "") == reg:
                return True
    return False


def registration_has_wake_debt(registration_id: str) -> bool:
    """True when CSR shows unpaid wake_owed or open stop_ack_owed for registration."""
    fold_pending_transitions()
    sessions = load_sessions()
    if get_open_wake_owed_for_registration(sessions, registration_id) is not None:
        return True
    return _registration_has_open_stop_ack_debt(sessions, registration_id)


def try_claim_wake_payment(*, thread: str, obligation_id: str) -> bool:
    """Claim open wake_owed for in-flight payment; idempotent False when claimed."""
    fold_pending_transitions()
    sessions = load_sessions()
    ob = get_open_wake_owed(sessions, thread=thread)
    if ob is None:
        return False
    oid = str(ob.get("obligation_id") or obligation_id)
    payment = ob.get("payment") or {}
    if payment.get("claimed") or payment.get("followup_ok"):
        return False
    append_session_transition_locked(
        {
            "event_id": f"wake.claim:{oid}",
            "event": "cse.wake.claimed",
            "ts": time.time(),
            "payload": {"thread": thread, "obligation_id": oid},
        }
    )
    return True


def release_lane_if_debt_cleared(
    registration_id: str, *, purpose: str | None = None
) -> bool:
    """Deregister lane when wake debt discharged; returns True if released."""
    reg_id = (registration_id or "").strip()
    if not reg_id or registration_has_wake_debt(reg_id):
        return False
    from claude_bundles.project_ask_abort import deregister_on_exit

    for reg in cdp_registry.list_active():
        if reg.registration_id == reg_id:
            deregister_on_exit(reg, purpose=purpose or reg.purpose)
            return True
    return False
