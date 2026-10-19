"""Conductor continuation events emitted after admit (handlers do not emit)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def FrontierConductorHopAdmitted(  # noqa: N802
    thread_id: str,
    work_key: str,
    hop_seq: int,
    hop_from: str,
    hop_reason: str,
    selected_seat_role: str,
    entry_gate_before: str,
    entry_gate_after: str,
    gate_advanced: bool,
    scoreboard_tip_sha_before: str,
    scoreboard_tip_sha_after: str,
    tip_mutated: bool,
    cumulative_row_hops: int,
    cumulative_row_input_tokens: int,
    cumulative_row_uncached_input_tokens: int,
) -> Event:
    """One admitted conductor continuation hop."""
    return Event(
        signal="frontier.conductor.hop.admitted",
        payload={
            "thread_id": thread_id,
            "work_key": work_key,
            "hop_seq": hop_seq,
            "hop_from": hop_from,
            "hop_reason": hop_reason,
            "selected_seat_role": selected_seat_role,
            "entry_gate_before": entry_gate_before,
            "entry_gate_after": entry_gate_after,
            "gate_advanced": gate_advanced,
            "scoreboard_tip_sha_before": scoreboard_tip_sha_before,
            "scoreboard_tip_sha_after": scoreboard_tip_sha_after,
            "tip_mutated": tip_mutated,
            "cumulative_row_hops": cumulative_row_hops,
            "cumulative_row_input_tokens": cumulative_row_input_tokens,
            "cumulative_row_uncached_input_tokens": (
                cumulative_row_uncached_input_tokens
            ),
        },
        role="coordination",
        scope="node",
    )


@event_factory
def FrontierConductorHopZeroProgress(  # noqa: N802
    thread_id: str,
    work_key: str,
    hop_seq: int,
    hop_from: str,
    tip_mutated: bool,
    child_dispatched: bool,
) -> Event:
    """Prior hop mutated no tip and dispatched no child. Does not block admit."""
    return Event(
        signal="frontier.conductor.hop.zero_progress",
        payload={
            "thread_id": thread_id,
            "work_key": work_key,
            "hop_seq": hop_seq,
            "hop_from": hop_from,
            "tip_mutated": tip_mutated,
            "child_dispatched": child_dispatched,
        },
        role="coordination",
        scope="node",
    )


def emit_conductor_hop_lifecycle(
    plan: dict[str, Any],
    publisher: Callable[[Event], None],
) -> None:
    """Emit hop.admitted once; zero_progress when both flags are false."""
    if plan.get("job") != "conductor":
        return
    hop_seq = plan.get("hop_seq")
    if not isinstance(hop_seq, int) or hop_seq < 1:
        return
    thread_id = str(plan.get("thread_id") or "")
    work_key = str(plan.get("work_key") or plan.get("source_ref") or "")
    hop_from = str(plan.get("hop_from") or "")
    publisher(
        FrontierConductorHopAdmitted(
            thread_id=thread_id,
            work_key=work_key,
            hop_seq=hop_seq,
            hop_from=hop_from,
            hop_reason=str(plan.get("hop_reason") or ""),
            selected_seat_role=str(plan.get("selected_seat_role") or ""),
            entry_gate_before=str(plan.get("entry_gate_before") or ""),
            entry_gate_after=str(plan.get("entry_gate") or ""),
            gate_advanced=bool(plan.get("gate_advanced")),
            scoreboard_tip_sha_before=str(plan.get("scoreboard_tip_sha_before") or ""),
            scoreboard_tip_sha_after=str(plan.get("scoreboard_tip_sha") or ""),
            tip_mutated=bool(plan.get("tip_mutated")),
            cumulative_row_hops=int(plan.get("cumulative_row_hops") or 0),
            cumulative_row_input_tokens=int(
                plan.get("cumulative_row_input_tokens") or 0
            ),
            cumulative_row_uncached_input_tokens=int(
                plan.get("cumulative_row_uncached_input_tokens") or 0
            ),
        )
    )
    if plan.get("tip_mutated") is False and plan.get("child_dispatched") is False:
        publisher(
            FrontierConductorHopZeroProgress(
                thread_id=thread_id,
                work_key=work_key,
                hop_seq=hop_seq,
                hop_from=hop_from,
                tip_mutated=False,
                child_dispatched=False,
            )
        )
