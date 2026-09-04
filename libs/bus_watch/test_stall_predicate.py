"""Tests for stall_predicate (Phase A)."""

from __future__ import annotations

from bus_watch.stall_predicate import mission_open, stall_predicate

_ALL_DONE_SCOREBOARD = """\
| G1 | Architecture | DONE |
| G2 | Frame | DONE |
| G3 | Implement | DONE |
"""

_OPEN_MISSION_SCOREBOARD = """\
| G1 | Architecture | DONE |
| G2 | Frame | DONE |
| G3 | Implement | OPEN |
"""

_PARKED_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: harvest G1
"""


def test_mission_open_false_when_all_done() -> None:
    assert not mission_open(scoreboard_body=_ALL_DONE_SCOREBOARD)


def test_mission_open_true_when_row_open() -> None:
    assert mission_open(scoreboard_body=_OPEN_MISSION_SCOREBOARD)


def test_stall_predicate_false_when_all_done() -> None:
    should, reason = stall_predicate(
        thread_snapshot={"status": "waiting", "turn_count": 50},
        scoreboard_body=_ALL_DONE_SCOREBOARD,
        closeout_body="stop: DONE\n",
    )
    assert not should
    assert reason == ""


def test_stall_predicate_true_parked_mission_open() -> None:
    should, reason = stall_predicate(
        thread_snapshot={"status": "predicate_unmet", "turn_count": 48},
        scoreboard_body=_OPEN_MISSION_SCOREBOARD,
        closeout_body=_PARKED_CLOSEOUT,
        predicate_unmet_slices=1,
    )
    assert should
    assert reason == "park_harvest_stall"


def test_stall_predicate_predicate_unmet_no_progress() -> None:
    should, reason = stall_predicate(
        thread_snapshot={"status": "predicate_unmet", "turn_count": 48},
        scoreboard_body="",
        closeout_body="",
        predicate_unmet_slices=2,
        last_turn_count=48,
    )
    assert should
    assert reason == "predicate_unmet_no_progress"
