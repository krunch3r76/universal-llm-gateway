"""Stall-pop predicate for attended bus consult watchers."""

from __future__ import annotations

from typing import Any

from claude_bundles.conductor_stop import parse_stop_tokens

from bus_watch.park_harvest import (
    mission_open,
    parked_or_none_next_admit,
    successor_owed,
)


def live_sdk_on_thread(*, thread_snapshot: dict[str, Any], thread_id: str = "") -> bool:
    """True when agent-bus snapshot indicates a non-terminal cursor-sdk dispatch."""
    _ = thread_id
    for key in ("live_sdk", "cursor_sdk_live", "sdk_live"):
        if thread_snapshot.get(key):
            return True
    live_count = thread_snapshot.get("live_dispatch_count")
    if isinstance(live_count, int) and live_count > 0:
        return True
    executions = thread_snapshot.get("active_executions") or thread_snapshot.get(
        "live_executions"
    )
    if isinstance(executions, list) and executions:
        return True
    if isinstance(executions, int) and executions > 0:
        return True
    return False


def stall_predicate(
    *,
    thread_snapshot: dict[str, Any],
    scoreboard_body: str = "",
    closeout_body: str = "",
    wait_slice_s: float = 20.0,
    predicate_unmet_slices: int = 0,
    last_turn_count: int | None = None,
) -> tuple[bool, str]:
    """Return (should_stall_pop, reason) for watcher incomplete branch."""
    closeout_tokens = parse_stop_tokens(closeout_body).tokens
    if "DONE" in closeout_tokens and not mission_open(scoreboard_body=scoreboard_body):
        return False, ""

    open_mission = mission_open(scoreboard_body=scoreboard_body) if scoreboard_body else True
    live = live_sdk_on_thread(thread_snapshot=thread_snapshot)
    owed = successor_owed(closeout_tokens=closeout_tokens)

    if not (open_mission and not live and not owed):
        return False, ""

    status = str(thread_snapshot.get("status") or "")
    turn_count = thread_snapshot.get("turn_count")
    if isinstance(turn_count, int):
        turn_count_i = turn_count
    else:
        try:
            turn_count_i = int(turn_count) if turn_count is not None else None
        except (TypeError, ValueError):
            turn_count_i = None

    park_context = parked_or_none_next_admit(body=closeout_body) or parked_or_none_next_admit(
        body=scoreboard_body
    )
    if park_context:
        return True, "park_harvest_stall"

    if status == "predicate_unmet" and predicate_unmet_slices >= 2:
        if last_turn_count is None or turn_count_i == last_turn_count:
            return True, "predicate_unmet_no_progress"

    if open_mission and not live and not owed and not scoreboard_body:
        if status == "predicate_unmet" and predicate_unmet_slices >= 2:
            return True, "predicate_unmet_no_progress"

    return False, ""
