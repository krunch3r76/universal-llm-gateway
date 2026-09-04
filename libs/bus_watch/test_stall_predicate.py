"""Tests for stall_predicate (Phase A)."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_bus_store.sdk_liveness import ProbeResult

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

_CLAIMED_PASS_SCOREBOARD = """\
| G1 | Architecture | DONE |
| G2 | Frame | DONE |
| G3 | Implement | DONE |
| G4 | Skeptic | DONE |
| G5 | Implement | DONE |
| G6 | Review | PASS |
| G7 | Land | CLAIMED |
"""

# F1 residual (G6 R2): only G7 non-DONE — regex must include G7 or mission_open false-closes at land gate.
_ONLY_G7_OPEN_SCOREBOARD = """\
| G1 | Architecture | DONE |
| G2 | Frame | DONE |
| G3 | Implement | DONE |
| G4 | Skeptic | DONE |
| G5 | Implement | DONE |
| G6 | Review | DONE |
| G7 | Land | OPEN |
"""

_PARKED_CLOSEOUT = """\
status: complete
stop: PARKED_TRANSPORT
CONSULT_PENDING
execution_id: exec-abc
poll_hint: wait
NEXT_ADMIT: harvest G1
"""

_A2_THREAD_ID = "thread-a2-fixture"


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _probe_not_live(_thread_id: str) -> ProbeResult:
    return ProbeResult(payload=None, http_status=404, error=None)


def _probe_running(_thread_id: str) -> ProbeResult:
    return ProbeResult(
        payload={
            "status": "running",
            "execution_id": "exec-abc",
            "last_heartbeat_at": _fresh_ts(),
        },
        http_status=200,
        error=None,
    )


def _probe_unreachable(_thread_id: str) -> ProbeResult:
    return ProbeResult(
        payload=None,
        http_status=None,
        error="probe_unreachable:timed out",
    )


def test_mission_open_false_when_all_done() -> None:
    assert not mission_open(scoreboard_body=_ALL_DONE_SCOREBOARD)


def test_mission_open_true_when_row_open() -> None:
    assert mission_open(scoreboard_body=_OPEN_MISSION_SCOREBOARD)


def test_mission_open_true_when_claimed_or_pass_not_only_open_literal() -> None:
    """F1 / A-1 false-negative wall: CLAIMED/PASS rows must keep mission open."""
    assert mission_open(scoreboard_body=_CLAIMED_PASS_SCOREBOARD)


def test_mission_open_true_when_only_g7_open() -> None:
    """F1 residual (G6 R2): G7-only open must keep mission open at the land gate."""
    assert mission_open(scoreboard_body=_ONLY_G7_OPEN_SCOREBOARD)


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
        thread_id=_A2_THREAD_ID,
        scoreboard_body=_OPEN_MISSION_SCOREBOARD,
        closeout_body=_PARKED_CLOSEOUT,
        predicate_unmet_slices=1,
        probe_fn=_probe_not_live,
    )
    assert should
    assert reason == "park_harvest_stall"


def test_stall_predicate_a9_live_probe_no_pop_on_a2_fixture() -> None:
    """S9 A-9 / W4′: live or unreachable probe must not pop on A-2 fixture."""
    base_kwargs = {
        "thread_snapshot": {"status": "predicate_unmet", "turn_count": 48},
        "thread_id": _A2_THREAD_ID,
        "scoreboard_body": _OPEN_MISSION_SCOREBOARD,
        "closeout_body": _PARKED_CLOSEOUT,
        "predicate_unmet_slices": 1,
    }
    should_running, reason_running = stall_predicate(
        **base_kwargs,
        probe_fn=_probe_running,
    )
    assert not should_running
    assert reason_running == ""

    should_unreachable, reason_unreachable = stall_predicate(
        **base_kwargs,
        probe_fn=_probe_unreachable,
    )
    assert not should_unreachable
    assert reason_unreachable == ""


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
