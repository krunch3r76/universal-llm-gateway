"""Stall-pop predicate for attended bus consult watchers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_bus_store.sdk_liveness import (
    LivenessVerdict,
    ProbeResult,
    classify_probe,
    probe_dispatch_status,
)
from claude_bundles.conductor_stop import parse_stop_tokens

from bus_watch.park_harvest import (
    mission_open,
    parked_or_none_next_admit,
    successor_owed,
)


def live_sdk_on_thread(
    *,
    thread_id: str,
    probe_fn: Callable[[str], ProbeResult] = probe_dispatch_status,
    link_execution_id: str | None = None,
) -> bool:
    """True when GIW dispatch-status indicates non-terminal cursor-sdk dispatch.

    Authority: ``probe_dispatch_status`` → ``classify_probe``.
    ``SKIP_LIVE`` and ``DEFER`` (fail-closed) ⇒ live; otherwise not live.
    """
    if not thread_id:
        return False
    probe = probe_fn(thread_id)
    verdict, _reason, _terminal = classify_probe(
        probe, link_execution_id=link_execution_id
    )
    return verdict in (LivenessVerdict.SKIP_LIVE, LivenessVerdict.DEFER)


def stall_predicate(
    *,
    thread_snapshot: dict[str, Any],
    thread_id: str = "",
    scoreboard_body: str = "",
    closeout_body: str = "",
    wait_slice_s: float = 20.0,
    predicate_unmet_slices: int = 0,
    last_turn_count: int | None = None,
    probe_fn: Callable[[str], ProbeResult] = probe_dispatch_status,
) -> tuple[bool, str]:
    """Return (should_stall_pop, reason) for watcher incomplete branch."""
    closeout_tokens = parse_stop_tokens(closeout_body).tokens
    if "DONE" in closeout_tokens and not mission_open(scoreboard_body=scoreboard_body):
        return False, ""

    open_mission = mission_open(scoreboard_body=scoreboard_body) if scoreboard_body else True
    live = live_sdk_on_thread(
        thread_id=thread_id,
        probe_fn=probe_fn,
    )
    owed = successor_owed(
        closeout_tokens=closeout_tokens,
        closeout_body=closeout_body,
    )

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
