"""In-process idempotency journal.

The store is a process-local dict. It does not survive a process restart.
There is no existing durable journal for these keys; a restart can accept
the same key again. Within one process, an unknown or in-flight slot blocks
a second send.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from chat_harvest.models import ClassifyRefuse

from interlocutor.targets import (
    CoworkMaestroTarget,
    CoworkUrlTarget,
    DisplayEightTarget,
    resolve_target,
)

_LOCK = threading.Lock()
_STORE: dict[tuple[str, str], dict[str, Any]] = {}

# Decisive refusals that happen before a click. A later same-key call may send.
_PRECLICK_CLEAR = frozenset(
    {
        "agent_id_required",
        "capability_unsupported",
        "composer_not_empty",
        "composer_text_mismatch",
        "composer_unconfirmed",
        "empty_message",
        "insertion_failed",
        "no_composer",
        "params_required",
        "target_mismatch",
        "target_required",
        "text_required",
        "unsupported_target",
        "unsupported_voice",
        "watermark_missing_authorization",
    }
)


def content_hash(*, text: str, voice: str) -> str:
    """Bind a steer to its text and voice. The target is stored beside this."""
    raw = f"{voice}\n{text}".encode()
    return hashlib.sha256(raw).hexdigest()


def canonical_target(target: str) -> str:
    """One slot id for aliases that resolve to the same endpoint."""
    resolved = resolve_target(target)
    if isinstance(resolved, ClassifyRefuse):
        return (target or "").strip()
    if isinstance(resolved, DisplayEightTarget):
        return f"display:{resolved.display}/{resolved.agent_id}/{resolved.app}"
    if isinstance(resolved, CoworkMaestroTarget):
        return f"cowork_maestro:{resolved.parent_thread}"
    if isinstance(resolved, CoworkUrlTarget):
        return f"cowork_url:{resolved.url.strip()}"
    return (target or "").strip()


def is_verified_steer_receipt(receipt: dict[str, Any]) -> bool:
    """True only for receipts that prove a verified send (safe to replay)."""
    if receipt.get("ok") is not True:
        return False
    inner = receipt.get("result")
    if isinstance(inner, dict) and inner.get("ok") is False:
        return False
    if receipt.get("adapter") == "display_eight":
        if receipt.get("delivered") is not True:
            return False
        if receipt.get("pasted") is False:
            return False
    if receipt.get("delivered") is False:
        return False
    return True


def reserve(
    *,
    target: str,
    idempotency_key: str | None,
    text: str,
    voice: str,
) -> dict[str, Any]:
    """Claim the key before a send, or refuse without sending.

    ``proceed`` means this caller owns the in-flight slot. ``duplicate`` is a
    verified replay. Any other action must not paste.
    """
    key = (idempotency_key or "").strip()
    if not key:
        return {"action": "proceed"}
    canon = canonical_target(target)
    digest = content_hash(text=text, voice=voice)
    with _LOCK:
        slot = _STORE.get((canon, key))
        if slot is None:
            _STORE[(canon, key)] = {
                "state": "in_flight",
                "digest": digest,
                "canon": canon,
            }
            return {"action": "proceed", "canon": canon}
        if slot.get("digest") != digest:
            return {"action": "conflict", "code": "idempotency_conflict"}
        state = str(slot.get("state") or "")
        if state == "verified" and is_verified_steer_receipt(slot.get("receipt") or {}):
            return {"action": "duplicate", "receipt": dict(slot.get("receipt") or {})}
        if state == "in_flight":
            return {"action": "in_flight", "code": "idempotency_in_flight"}
        if state == "unknown":
            return {"action": "tombstone", "code": "idempotency_unknown"}
        del _STORE[(canon, key)]
        _STORE[(canon, key)] = {
            "state": "in_flight",
            "digest": digest,
            "canon": canon,
        }
        return {"action": "proceed", "canon": canon}


def _preclick_clear(receipt: dict[str, Any]) -> bool:
    if receipt.get("clicked_unknown"):
        return False
    if str(receipt.get("race_winner") or "") == "timer":
        return False
    if receipt.get("send_clicked") is True:
        return False
    if str(receipt.get("phase") or "") == "send_unknown":
        return False
    code = str(receipt.get("code") or receipt.get("reason") or "")
    return code in _PRECLICK_CLEAR


def settle(
    *,
    target: str,
    idempotency_key: str | None,
    text: str,
    voice: str,
    receipt: dict[str, Any],
) -> None:
    """Record a verified send, drop a pre-click refusal, or tombstone the rest."""
    key = (idempotency_key or "").strip()
    if not key:
        return
    canon = canonical_target(target)
    digest = content_hash(text=text, voice=voice)
    with _LOCK:
        slot = _STORE.get((canon, key))
        if slot is None or slot.get("digest") != digest:
            return
        if is_verified_steer_receipt(receipt):
            slot["state"] = "verified"
            slot["receipt"] = dict(receipt)
            return
        if _preclick_clear(receipt):
            del _STORE[(canon, key)]
            return
        slot["state"] = "unknown"
        slot["receipt"] = dict(receipt)


def lookup(*, target: str, idempotency_key: str | None) -> dict[str, Any] | None:
    """Return a verified receipt only. Unknown slots are not successes."""
    key = (idempotency_key or "").strip()
    if not key:
        return None
    canon = canonical_target(target)
    with _LOCK:
        slot = _STORE.get((canon, key))
        if slot is None:
            return None
        if slot.get("state") == "verified" and is_verified_steer_receipt(
            slot.get("receipt") or {}
        ):
            return dict(slot.get("receipt") or {})
        return None


def remember(
    *, target: str, idempotency_key: str | None, receipt: dict[str, Any]
) -> None:
    """Test helper. Unverified shapes are not stored, so they cannot replay."""
    key = (idempotency_key or "").strip()
    if not key or not is_verified_steer_receipt(receipt):
        return
    canon = canonical_target(target)
    with _LOCK:
        _STORE[(canon, key)] = {
            "state": "verified",
            "digest": content_hash(text="", voice=""),
            "canon": canon,
            "receipt": dict(receipt),
        }


def clear_for_tests() -> None:
    with _LOCK:
        _STORE.clear()


__all__ = [
    "canonical_target",
    "clear_for_tests",
    "content_hash",
    "is_verified_steer_receipt",
    "lookup",
    "remember",
    "reserve",
    "settle",
]
