"""Gear-3 spawn-on-wake predicate, event gates, and fire leg."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bus_watch.spawn_wake.fire import tick_spawn_on_wake as _tick_spawn_on_wake_impl
from bus_watch.spawn_wake.predicate import compute_spawn_signal_sources

_BLOCKER_MARKERS = re.compile(
    r"BLOCKED|FAILED|OPERATOR_GATE|HOLD_MERGE|checks_failed", re.I
)
_LIAISON_TICKER = "liaison-ticker"
_DEFAULT_OVERNIGHT_QUIET = (23, 7)
_REAL_WAKE_SOURCES = frozenset(
    {
        "actionable_attention",
        "review_apply",
        "handoff",
        "context_budget",
    }
)


def overnight_quiet_enabled(policy: dict[str, Any] | None = None) -> bool:
    """True when overnight quiet hours apply (default on)."""
    env = os.environ.get("LIAISON_OVERNIGHT_QUIET", "1").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if policy is not None and policy.get("overnight_quiet_hours") is False:
        return False
    return True


def is_overnight_quiet(
    *,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> bool:
    """True during the configured quiet window (default 23:00–07:00 local)."""
    if not overnight_quiet_enabled(policy):
        return False
    quiet = (policy or {}).get("overnight_quiet_hours")
    if quiet is False:
        return False
    if isinstance(quiet, (list, tuple)) and len(quiet) >= 2:
        start_h, end_h = int(quiet[0]), int(quiet[1])
    else:
        start_h, end_h = _DEFAULT_OVERNIGHT_QUIET
    tz_name = str(
        (policy or {}).get("overnight_tz")
        or os.environ.get("LIAISON_OVERNIGHT_TZ")
        or "America/Los_Angeles"
    )
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — bad tz name falls back to system local
        tz = datetime.now().astimezone().tzinfo or UTC
    instant = now if now is not None else datetime.now(tz)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=tz)
    else:
        instant = instant.astimezone(tz)
    hour = instant.hour
    if start_h > end_h:
        return hour >= start_h or hour < end_h
    return start_h <= hour < end_h


def _lane_row_is_blocker(item: dict[str, Any]) -> bool:
    if str(item.get("lifecycle") or "").lower() == "failed":
        return True
    subject = str(item.get("last_subject") or "")
    return bool(_BLOCKER_MARKERS.search(subject))


def _digest_has_blocker(digest: dict[str, Any]) -> bool:
    budget = digest.get("budget") or {}
    stop_class = str(budget.get("stop_class") or "")
    if stop_class and _BLOCKER_MARKERS.search(stop_class):
        return True
    attention = digest.get("attention") or []
    if isinstance(attention, list):
        for item in attention:
            if isinstance(item, dict) and _lane_row_is_blocker(item):
                return True
    lanes = digest.get("lanes") or []
    if isinstance(lanes, list):
        for row in lanes:
            if not isinstance(row, dict):
                continue
            if int(row.get("unread") or 0) <= 0:
                continue
            if _lane_row_is_blocker(row):
                return True
    return False


def has_open_request(digest: dict[str, Any]) -> bool:
    """Unread operator REQUEST on the continuity root (not liaison-ticker noise)."""
    root = digest.get("root") or {}
    recent = root.get("recent_turns")
    if not isinstance(recent, list):
        return False
    for turn in recent:
        if not isinstance(turn, dict):
            continue
        from_agent = str(turn.get("from") or turn.get("from_agent") or "").strip()
        if from_agent == _LIAISON_TICKER:
            continue
        subject = str(turn.get("subject") or "").strip().upper()
        if subject.startswith("REQUEST") or "REQUEST|" in subject:
            return True
    return False


def has_real_wake_event(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    lock: dict[str, Any] | None = None,
    now: float | None = None,
) -> bool:
    """True when the digest carries land, VERDICT, blocker, or actionable work — not idle CP."""
    sources = compute_spawn_signal_sources(
        digest, state, lock=lock, now=now
    )
    if any(name in _REAL_WAKE_SOURCES for name in sources):
        return True
    if _digest_has_blocker(digest):
        return True
    return False


def has_open_request_or_event(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    lock: dict[str, Any] | None = None,
    now: float | None = None,
) -> bool:
    return has_open_request(digest) or has_real_wake_event(
        digest, state, lock=lock, now=now
    )


def attended_loop_may_wake(
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    budget_stop_new: bool = False,
) -> bool:
    """Attended ``--loop`` may emit a sentinel only on real events (or fresh budget stop)."""
    if budget_stop_new:
        return True
    return has_open_request_or_event(digest, state)


def attended_tick_due(
    *,
    changed_since_last_tick: bool,
    digest: dict[str, Any],
    state: dict[str, Any],
    budget_stop_new: bool,
    digest_is_own_echo: bool,
) -> bool:
    """Whether attended ``--loop`` may emit ``AGENT_LOOP_TICK_liaison`` (never idle heartbeat)."""
    changed_real = (
        changed_since_last_tick
        and not digest_is_own_echo
        and attended_loop_may_wake(digest, state, budget_stop_new=budget_stop_new)
    )
    return changed_real or budget_stop_new


def _event_only_spawn_gate_hold(
    digest: dict[str, Any],
    state: dict[str, Any],
    root_id: str,
) -> dict[str, Any] | None:
    from bus_watch.fable_lock import read_lock
    from bus_watch.spawn_pending import handoff_wake

    if handoff_wake(state):
        return None
    lock = read_lock(root_id)
    if has_open_request_or_event(digest, state, lock=lock):
        return None
    return {
        "spawned": False,
        "action": "hold",
        "reason": "no_open_request_or_event",
    }


def tick_spawn_on_wake(
    digest: dict[str, Any],
    state: dict[str, Any],
    root_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Gear-3 poll with overnight and event-only gates before spawn evaluation."""
    policy = digest.get("policy") or {}
    if is_overnight_quiet(policy=policy):
        return {"spawned": False, "action": "hold", "reason": "overnight_disabled"}
    gate_hold = _event_only_spawn_gate_hold(digest, state, root_id)
    had_pending = bool(state.get("pending_spawn"))
    if gate_hold is not None and not had_pending:
        return gate_hold
    impl_kwargs = kwargs
    if gate_hold is not None and had_pending:
        impl_kwargs = {**kwargs, "dry_run": True}
    result = _tick_spawn_on_wake_impl(digest, state, root_id, **impl_kwargs)
    if hold := _event_only_spawn_gate_hold(digest, state, root_id):
        evaluation = result.get("evaluation") or {}
        if result.get("action") in ("spawned", "would_spawn") or evaluation.get("spawn"):
            return {**hold, "evaluation": evaluation}
    return result


from bus_watch.spawn_wake import (  # noqa: E402 — re-exports after gate API
    _WORK_KEY_IN_FLIGHT,
    SUCCESSOR_MESSAGE_CAP,
    _wire_submit_body,
    build_dispatch_body,
    build_successor_message,
    evaluate_spawn_predicate,
    fire_spawn,
    spawn_fingerprint,
    successor_context_from_digest,
)

__all__ = [
    "SUCCESSOR_MESSAGE_CAP",
    "_WORK_KEY_IN_FLIGHT",
    "_wire_submit_body",
    "attended_loop_may_wake",
    "attended_tick_due",
    "build_dispatch_body",
    "build_successor_message",
    "evaluate_spawn_predicate",
    "fire_spawn",
    "has_open_request",
    "has_open_request_or_event",
    "has_real_wake_event",
    "is_overnight_quiet",
    "overnight_quiet_enabled",
    "spawn_fingerprint",
    "successor_context_from_digest",
    "tick_spawn_on_wake",
]
