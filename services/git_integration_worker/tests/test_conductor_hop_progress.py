"""Hop progress signature — advance detection and loop provability (a:32411)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    HopProgressSignature,
    next_admit_in_closeout,
    progress_signature_for_row,
    signature_advanced,
    signature_can_prove_loop,
)

pytestmark = pytest.mark.offline


def _sig(
    *,
    entry_gate: str = "G1",
    witnessed_done: tuple[str, ...] = (),
    lane_tip: str | None = None,
    next_admit: str | None = None,
) -> HopProgressSignature:
    return HopProgressSignature(
        entry_gate=entry_gate,
        witnessed_done=frozenset(witnessed_done),
        lane_tip=lane_tip,
        next_admit=next_admit,
    )


def test_entry_gate_move_is_advance() -> None:
    assert signature_advanced(_sig(entry_gate="G4"), _sig(entry_gate="G1")) is True


def test_witness_growth_is_advance() -> None:
    newer = _sig(witnessed_done=("G1", "G2"))
    older = _sig(witnessed_done=("G1",))
    assert signature_advanced(newer, older) is True


def test_witness_shrink_is_not_advance() -> None:
    """A retracted witness is a rewind, not progress."""
    newer = _sig(witnessed_done=("G1",))
    older = _sig(witnessed_done=("G1", "G2"))
    assert signature_advanced(newer, older) is False


def test_lane_tip_move_is_advance() -> None:
    newer = _sig(lane_tip="cd5cf10a")
    older = _sig(lane_tip="e96037df")
    assert signature_advanced(newer, older) is True


def test_lane_tip_absent_on_one_side_is_not_advance() -> None:
    """A missing tip is unknown, not movement."""
    assert signature_advanced(_sig(lane_tip="cd5cf10a"), _sig()) is False


def test_next_admit_move_is_advance() -> None:
    newer = _sig(next_admit="harvest G4")
    older = _sig(next_admit="harvest G3")
    assert signature_advanced(newer, older) is True


def test_identical_signatures_are_not_advance() -> None:
    stuck = _sig(entry_gate="G1", lane_tip="cd5cf10a")
    assert signature_advanced(stuck, stuck) is False


def test_empty_fold_alone_cannot_prove_loop() -> None:
    """The a:32411 shape — G1 with nothing ever witnessed and no other signal."""
    assert signature_can_prove_loop(_sig(), _sig()) is False


def test_entry_gate_alone_cannot_prove_loop() -> None:
    """A gate word without a witness is a table projection, not a measurement."""
    both = _sig(entry_gate="G4")
    assert signature_can_prove_loop(both, both) is False


def test_any_witness_proves_the_fold_is_paid() -> None:
    assert signature_can_prove_loop(_sig(witnessed_done=("G1",)), _sig()) is True


def test_lane_tip_on_both_sides_proves_loop() -> None:
    both = _sig(lane_tip="cd5cf10a")
    assert signature_can_prove_loop(both, both) is True


def test_next_admit_on_both_sides_proves_loop() -> None:
    both = _sig(next_admit="harvest G1")
    assert signature_can_prove_loop(both, both) is True


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("stop: ROW_HOP\nNEXT_ADMIT: harvest G1\n", "harvest G1"),
        ("NEXT_ADMIT: none", "none"),
        ("  NEXT_ADMIT : harvest G4  ", "harvest G4"),
        ("NEXT_ADMIT", None),
        ("", None),
        ("no admit named here", None),
    ],
)
def test_next_admit_in_closeout(body: str, expected: str | None) -> None:
    assert next_admit_in_closeout(body) == expected


def test_signature_for_row_reads_stamps_without_touching_the_fold() -> None:
    row = {
        "work_key": "todo:conductor-hop-wait-protocol",
        "thread_id": "10128",
        "source_repo": "/nonexistent-repo",
        "record_json": (
            '{"hop_entry_gate":"G1","hop_witnessed_done":[],'
            '"hop_lane_tip":"cd5cf10a","hop_next_admit":"harvest G1"}'
        ),
    }
    signature = progress_signature_for_row(row)
    assert signature.entry_gate == "G1"
    assert signature.witnessed_done == frozenset()
    assert signature.lane_tip == "cd5cf10a"
    assert signature.next_admit == "harvest G1"


def test_historical_signature_does_not_live_read_lane_tip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstamped priors stay tip-less even when git would return today's head."""
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress.read_lane_tip",
        lambda **_kwargs: "d34aacd2deadbeef",
    )
    row = {
        "work_key": "todo:conductor-hop-wait-protocol",
        "thread_id": "10128",
        "source_repo": "/repo",
        "record_json": '{"hop_entry_gate":"G1","hop_witnessed_done":[]}',
    }
    live = progress_signature_for_row(row, live=True)
    historical = progress_signature_for_row(row, live=False)
    assert live.lane_tip == "d34aacd2deadbeef"
    assert historical.lane_tip is None
