"""Specimen replay integration test — agent-bus:10142 turns 57→60 (A11)."""

from __future__ import annotations

import io
from typing import Any

from bus_watch.producer_grace import ProducerGrace
from bus_watch.stall_pop import emit_stall_pop, should_emit_stall_pop
from bus_watch.stall_predicate import stall_predicate

# Gate-0 captured dispatch link row (thread 10142, FA-1 PASS 2026-09-07).
_GATE0_LINK_ROW = {
    "execution_id": "d6a93d64-18a9-4779-8238-89d6af49e415",
    "pipeline_id": "cdp-generate",
    "linked_at": "2026-09-07T05:41:20Z",
    "terminal_status": None,
    "delivery_at": None,
}

_GATE0_PRODUCER = {
    "execution_id": _GATE0_LINK_ROW["execution_id"],
    "pipeline_id": _GATE0_LINK_ROW["pipeline_id"],
    "state": "in_flight",
    "terminal_status": None,
    "linked_at": _GATE0_LINK_ROW["linked_at"],
    "delivery_at": None,
    "source": "thread_dispatch_links",
}


def _replay_incomplete_slice(
    *,
    snap: dict[str, Any],
    grace: ProducerGrace,
    predicate_unmet_slices: int,
    last_turn_count: int | None,
    pops: list[str],
    last_stall_reason: str | None,
) -> tuple[int, int | None, str | None]:
    turn_count = int(snap["turn_count"])
    producer = snap.get("producer") or {}
    grace_expired = grace.observe(producer, turn_count)
    should_pop, reason = stall_predicate(
        thread_snapshot=snap,
        thread_id="10142",
        scoreboard_body="",
        closeout_body="",
        predicate_unmet_slices=predicate_unmet_slices,
        last_turn_count=last_turn_count,
        producer=producer,
        producer_grace_expired=grace_expired,
    )
    next_last_turn = turn_count
    if should_pop and reason:
        emit, next_reason = should_emit_stall_pop(
            last_reason=last_stall_reason,
            reason=reason,
            stall_active=True,
        )
        if emit:
            pops.append(reason)
            last_stall_reason = next_reason
    return predicate_unmet_slices, next_last_turn, last_stall_reason


def test_specimen_10142_replay_zero_pops_until_grace_expiry() -> None:
    """Chrome stub + in_flight producer: suppress until grace, one pop at expiry."""
    clock = {"now": 0.0}

    def now_fn() -> float:
        return clock["now"]

    grace = ProducerGrace(grace_seconds=900.0, now_fn=now_fn)
    pops: list[str] = []
    last_stall_reason: str | None = None
    predicate_unmet_slices = 0
    last_turn_count: int | None = None

    snapshots = [
        {
            "status": "predicate_unmet",
            "turn_count": 58,
            "thread_status": "active",
            "producer": _GATE0_PRODUCER,
        },
        {
            "status": "predicate_unmet",
            "turn_count": 60,
            "thread_status": "active",
            "producer": _GATE0_PRODUCER,
        },
    ]

    for snap in snapshots:
        turn_count = int(snap["turn_count"])
        if snap["status"] == "predicate_unmet":
            if last_turn_count is not None and turn_count == last_turn_count:
                predicate_unmet_slices += 1
            else:
                predicate_unmet_slices = 1
        else:
            predicate_unmet_slices = 0
        predicate_unmet_slices, last_turn_count, last_stall_reason = _replay_incomplete_slice(
            snap=snap,
            grace=grace,
            predicate_unmet_slices=predicate_unmet_slices,
            last_turn_count=last_turn_count,
            pops=pops,
            last_stall_reason=last_stall_reason,
        )
        clock["now"] += 20.0

    assert pops == []

    settled_snap = {
        "status": "predicate_unmet",
        "turn_count": 60,
        "thread_status": "active",
        "producer": _GATE0_PRODUCER,
    }
    for _ in range(45):
        predicate_unmet_slices += 1
        predicate_unmet_slices, last_turn_count, last_stall_reason = _replay_incomplete_slice(
            snap=settled_snap,
            grace=grace,
            predicate_unmet_slices=predicate_unmet_slices,
            last_turn_count=last_turn_count,
            pops=pops,
            last_stall_reason=last_stall_reason,
        )
        clock["now"] += 20.0

    assert pops == ["predicate_unmet_no_progress"]
    assert "producer_terminal_no_reply" not in pops


def test_emit_stall_pop_reason_transition_not_debounced_as_duplicate() -> None:
    emit, last = should_emit_stall_pop(
        last_reason="predicate_unmet_no_progress",
        reason="producer_terminal_no_reply",
        stall_active=True,
    )
    assert emit
    assert last == "producer_terminal_no_reply"
    buf = io.StringIO()
    emit_stall_pop("producer_terminal_no_reply", stream=buf)
    assert buf.getvalue().strip() == "stall-pop: producer_terminal_no_reply"
