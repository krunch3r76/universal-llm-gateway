"""Process-local spawn slot. Not durable across restarts or processes.

A reused key after process death starts at ``creating`` again. This module
does not claim exactly-once delivery.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

_LOCK = threading.Lock()
_SLOTS: dict[str, dict[str, Any]] = {}

_NON_RETRYABLE = frozenset(
    {
        "cdp_eval_timeout",
        "clicked_unknown",
        "delivery_unconfirmed",
        "send_unconfirmed",
        "unknown",
    }
)


def clear_spawn_cache_for_tests() -> None:
    """Drop every slot. Tests only."""
    with _LOCK:
        _SLOTS.clear()


def _new_slot(digest: str, nonce: str, token: str) -> dict[str, Any]:
    return {
        "state": "creating",
        "digest": digest,
        "owner": token,
        "nonce": nonce,
        "agent_id": "",
        "hide_ok": False,
        "sub_done": False,
        "steer_phase": "not_sent",
        "receipt": None,
    }


def begin(key: str, digest: str) -> dict[str, Any]:
    """Claim ``key``. A second owner receives ``idempotency_in_flight``."""
    token = uuid.uuid4().hex
    with _LOCK:
        slot = _SLOTS.get(key)
        if slot is None:
            _SLOTS[key] = _new_slot(digest, key, token)
            return {"action": "create", "token": token, "nonce": key}
        if slot.get("digest") != digest:
            return {"action": "conflict", "code": "idempotency_conflict"}
        if slot.get("state") == "verified":
            return {"action": "duplicate", "receipt": dict(slot.get("receipt") or {})}
        if slot.get("state") == "create_unknown":
            return {
                "action": "blocked",
                "code": "create_unknown",
                "needs_operator": True,
            }
        phase = str(slot.get("steer_phase") or "not_sent")
        if phase in {"attempting", "sent_unknown"}:
            return {
                "action": "blocked",
                "code": "initial_text_unconfirmed",
                "needs_operator": True,
                "agent_id": slot.get("agent_id"),
            }
        if slot.get("owner"):
            return {"action": "in_flight", "code": "idempotency_in_flight"}
        if slot.get("state") == "created":
            slot["state"] = "resuming"
            slot["owner"] = token
            return {
                "action": "resume",
                "token": token,
                "nonce": slot.get("nonce"),
                "agent_id": slot.get("agent_id"),
                "hide_ok": bool(slot.get("hide_ok")),
                "sub_done": bool(slot.get("sub_done")),
                "steer_phase": phase,
            }
        return {"action": "in_flight", "code": "idempotency_in_flight"}


def note(key: str, token: str, **fields: Any) -> None:
    """Record phase fields while ``token`` still owns the slot."""
    with _LOCK:
        slot = _SLOTS.get(key)
        if slot is None or slot.get("owner") != token:
            return
        slot.update(fields)


def mark_steer(key: str, token: str, phase: str) -> None:
    """Write ``steer_phase`` before or after the steer call."""
    note(key, token, steer_phase=phase)


def release(key: str, token: str, *, outcome: str, receipt: dict[str, Any] | None = None) -> None:
    """Drop ownership. Only the matching token may change the phase."""
    with _LOCK:
        slot = _SLOTS.get(key)
        if slot is None or slot.get("owner") != token:
            return
        slot["owner"] = None
        if outcome == "verified":
            slot["state"] = "verified"
            slot["receipt"] = dict(receipt or {})
            slot["steer_phase"] = "delivered"
        elif outcome == "create_unknown":
            slot["state"] = "create_unknown"
        elif outcome == "drop":
            _SLOTS.pop(key, None)
        else:
            slot["state"] = "created"


def known_not_sent(result: dict[str, Any]) -> bool:
    """True only when the receipt proves the text was not submitted."""
    if result.get("delivered") is not False:
        return False
    if result.get("pasted") is not False:
        return False
    if result.get("retryable") is False:
        return False
    if result.get("clicked_unknown"):
        return False
    if result.get("send_clicked") is True:
        return False
    code = str(result.get("code") or result.get("reason") or "")
    return code not in _NON_RETRYABLE


__all__ = [
    "begin",
    "clear_spawn_cache_for_tests",
    "known_not_sent",
    "mark_steer",
    "note",
    "release",
]
