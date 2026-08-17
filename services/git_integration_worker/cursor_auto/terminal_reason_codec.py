"""Encode durable ``terminal_reason`` values for cursor-auto job terminalization."""

from __future__ import annotations

from typing import Any

TERMINAL_REASON_MAX_LEN = 512

TERMINAL_REASON_RECONCILE_INFLIGHT_LOST = "reconcile_inflight_lost"
TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED = "restart_reconcile_superseded_by_later_turn"
TERMINAL_REASON_CONFER_RELAY_FAILED = "confer_relay_failed"
TERMINAL_REASON_CLOSEOUT_RELAY_FAILED = "closeout_relay_failed"
TERMINAL_REASON_DELIBERATE_FALLBACK = "deliberate_failure"


def truncate_terminal_reason(
    reason: str, *, max_len: int = TERMINAL_REASON_MAX_LEN
) -> str:
    """Bound persisted reasons; never drop the exception type prefix."""
    if len(reason) <= max_len:
        return reason
    return reason[: max_len - 3] + "..."


def format_exception_reason(exc: BaseException) -> str:
    """Persist exception type and message for worker catch paths."""
    name = type(exc).__name__
    msg = str(exc).strip()
    combined = f"{name}: {msg}" if msg else name
    return truncate_terminal_reason(combined)


def deliberate_failure_terminal_reason(
    *,
    disposition: str | None = None,
    payload: dict[str, Any] | None = None,
    summary: str | None = None,
) -> str:
    """Derive a durable reason when the seat deliberately terminalizes as failed."""
    payload = payload or {}
    for key in ("reason", "declined_reason", "legacy_reason"):
        val = payload.get(key)
        if val is not None and str(val).strip():
            return truncate_terminal_reason(str(val).strip())
    if disposition:
        return truncate_terminal_reason(f"disposition:{disposition}")
    if summary:
        return truncate_terminal_reason(summary.strip())
    return TERMINAL_REASON_DELIBERATE_FALLBACK


def relay_failure_terminal_reason(
    relay: dict[str, Any], *, fallback: str
) -> str:
    """Name a nested relay failure without dropping the relay's own hint."""
    hint = relay.get("reason") or relay.get("skipped")
    if hint is not None and str(hint).strip():
        return truncate_terminal_reason(f"{fallback}:{str(hint).strip()}")
    return fallback


__all__ = [
    "TERMINAL_REASON_CLOSEOUT_RELAY_FAILED",
    "TERMINAL_REASON_CONFER_RELAY_FAILED",
    "TERMINAL_REASON_DELIBERATE_FALLBACK",
    "TERMINAL_REASON_MAX_LEN",
    "TERMINAL_REASON_RECONCILE_INFLIGHT_LOST",
    "TERMINAL_REASON_RESTART_RECONCILE_SUPERSEDED",
    "deliberate_failure_terminal_reason",
    "format_exception_reason",
    "relay_failure_terminal_reason",
    "truncate_terminal_reason",
]
