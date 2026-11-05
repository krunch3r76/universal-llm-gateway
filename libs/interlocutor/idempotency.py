"""In-process idempotency journal (slice 1 — non-durable)."""

from __future__ import annotations

from typing import Any

_STORE: dict[tuple[str, str], dict[str, Any]] = {}


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
    return True


def lookup(*, target: str, idempotency_key: str | None) -> dict[str, Any] | None:
    key = (idempotency_key or "").strip()
    if not key:
        return None
    slot = (target.strip(), key)
    receipt = _STORE.get(slot)
    if receipt is None:
        return None
    if not is_verified_steer_receipt(receipt):
        del _STORE[slot]
        return None
    return receipt


def remember(
    *, target: str, idempotency_key: str | None, receipt: dict[str, Any]
) -> None:
    key = (idempotency_key or "").strip()
    if not key:
        return
    _STORE[(target.strip(), key)] = dict(receipt)


def clear_for_tests() -> None:
    _STORE.clear()


__all__ = ["clear_for_tests", "is_verified_steer_receipt", "lookup", "remember"]
