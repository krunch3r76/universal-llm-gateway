"""In-process idempotency journal (slice 1 — non-durable)."""

from __future__ import annotations

from typing import Any

_STORE: dict[tuple[str, str], dict[str, Any]] = {}


def lookup(*, target: str, idempotency_key: str | None) -> dict[str, Any] | None:
    key = (idempotency_key or "").strip()
    if not key:
        return None
    return _STORE.get((target.strip(), key))


def remember(*, target: str, idempotency_key: str | None, receipt: dict[str, Any]) -> None:
    key = (idempotency_key or "").strip()
    if not key:
        return
    _STORE[(target.strip(), key)] = dict(receipt)


def clear_for_tests() -> None:
    _STORE.clear()


__all__ = ["clear_for_tests", "lookup", "remember"]
