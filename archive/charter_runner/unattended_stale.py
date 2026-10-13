"""Unattended stale-window hard-fail for the per-root admission mode."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from scripts.model_manager import observation_event as events

from .admission import CapStore, Decision, evaluate_root
from .kernel.host import DEFAULT_AUTONOMOUS_STALE_S
from .state_close import maybe_state_close_root

_ENV_UNATTENDED_STALE_S = "CHARTER_UNATTENDED_STALE_S"
_WAITING_OPEN_REMIND_S = 900.0
_reminded: set[str] = set()


def stall_seconds(
    *,
    admission_mode: str,
    unattended_stale_override: float | None,
) -> float:
    """Stall seconds from the mode the tick resolved for this root.

    Constructor override wins, then ``CHARTER_UNATTENDED_STALE_S`` when it
    parses as a float, then 3600 for ``autonomous``, else 0. Does not read
    ``CHARTER_ADMISSION_MODE`` or the arming file.
    """
    if unattended_stale_override is not None:
        return max(0.0, float(unattended_stale_override))
    raw = os.environ.get(_ENV_UNATTENDED_STALE_S, "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    if admission_mode == "autonomous":
        return DEFAULT_AUTONOMOUS_STALE_S
    return 0.0


def _admission_posted_at(admission_turn: dict) -> datetime | None:
    try:
        meta = json.loads(str(admission_turn.get("body") or ""))
        raw = meta.get("posted_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


async def maybe_apply_stale_window_stop(
    *,
    root_id: str,
    turns: list[dict[str, Any]],
    caps: CapStore,
    admission_mode: str,
    unattended_stale_override: float | None,
    state_closes_this_tick: int,
    max_state_closes: int,
) -> int:
    """Soft waiting_open remind or hard stale_window stop for in-flight windows."""
    decision = evaluate_root(root_id, turns, caps)
    if decision.eligible or decision.reason != "window_in_flight":
        return state_closes_this_tick

    allowed, cap_reason = caps.check(root_id)
    if not allowed and cap_reason == "stopped:stale_window":
        # A4: the root was marked on an earlier tick that already spent the
        # close budget. This tick finishes close→unenroll without a second fail.
        return await maybe_state_close_root(
            decision,
            reason="stale_window",
            state_closes_this_tick=state_closes_this_tick,
            max_state_closes=max_state_closes,
        )
    if not allowed and (cap_reason or "").startswith("stopped:"):
        return state_closes_this_tick

    adm = decision.admission_turn or {}
    posted_at = _admission_posted_at(adm)
    if posted_at is None:
        return state_closes_this_tick

    age = (datetime.now(UTC) - posted_at).total_seconds()
    stale_s = stall_seconds(
        admission_mode=admission_mode,
        unattended_stale_override=unattended_stale_override,
    )
    if stale_s > 0 and age >= stale_s:
        caps.mark_failed(root_id, "stale_window")
        await events.emit_manage_charter_tick_window_failed(
            root=root_id, reason="stale_window"
        )
        stale_decision = Decision(
            eligible=False,
            reason="stale_window",
            root_id=root_id,
            checkpoint=decision.checkpoint,
            parsed=decision.parsed,
            admission_turn=decision.admission_turn,
        )
        return await maybe_state_close_root(
            stale_decision,
            reason="stale_window",
            state_closes_this_tick=state_closes_this_tick,
            max_state_closes=max_state_closes,
        )

    if age < _WAITING_OPEN_REMIND_S:
        return state_closes_this_tick
    if root_id in _reminded:
        return state_closes_this_tick
    _reminded.add(root_id)
    await events.emit_manage_charter_tick_waiting_open(root=root_id, age_s=int(age))
    return state_closes_this_tick


__all__ = ["maybe_apply_stale_window_stop", "stall_seconds"]
