"""Unverifiable vs CSE-death stall classification (a:30678 / a:28790)."""

from __future__ import annotations

from typing import Any

from cdp_ask.models import StallStage, classify_stall_stage

UNVERIFIABLE_STALL_STAGES = frozenset(
    {
        "observer_unverified",
        "horizon_unverifiable_retained",
        "reconcile_abandoned_unverifiable",
        "archive_write",
    }
)

DEATH_STALL_STAGES = frozenset(
    {
        "weekly_limit",
        "upstream_overloaded",
        "worker_cancelled",
        "mark_terminal",
        "completion_detection",
    }
)

_DEATH_ERROR_TOKENS = (
    "weekly limit",
    "hit a limit",
    "overloaded",
    "error_banner",
    "aborted",
    "cancelled",
    "worker_crash",
    "worker_cancelled",
)


def is_unverifiable_stall(
    stall_stage: str | None, error: str | None = None
) -> bool:
    """True when a failed snapshot is observer-unverifiable, not CSE death."""
    stage = (stall_stage or "").strip()
    if stage in DEATH_STALL_STAGES:
        return False
    if stage in UNVERIFIABLE_STALL_STAGES:
        return True
    err = (error or "").lower()
    if any(token in err for token in _DEATH_ERROR_TOKENS):
        return False
    return stage in {"", "unknown"}


def converse_fail_error(last_error: str | None) -> str:
    """Keep inner harness error; generic token is fallback only."""
    raw = (last_error or "").strip()
    return raw if raw else "conversation failed"


def converse_stall_stage(
    last_error: str | None, *, conv_ok: bool
) -> StallStage | None:
    """Stall for a converse payload — generic unknown becomes observer_unverified."""
    if conv_ok:
        return None
    stall = classify_stall_stage(converse_fail_error(last_error))
    if stall == "unknown":
        return "observer_unverified"
    return stall


def failed_snapshot_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a failed satellite snapshot onto stall, error, and extras."""
    stall = snapshot.get("stall_stage")
    error = str(snapshot.get("error") or snapshot.get("status") or "")
    unverifiable = is_unverifiable_stall(stall, error)
    if unverifiable and (not stall or stall == "unknown"):
        stall = "observer_unverified"
    extras: dict[str, Any] = {}
    url = snapshot.get("url")
    if url:
        extras["chat_url"] = url
    return {
        "stall_stage": stall,
        "error": error,
        "unverifiable": unverifiable,
        "retain_reason": "observer_unverified" if unverifiable else None,
        "extras": extras,
    }
