"""Tests for purpose-aware CDP lane admission (class ceilings + transitional additive)."""

from __future__ import annotations

from typing import Any

import pytest

from cdp_ask.lane_admission import (
    ADVISOR_RESERVE,
    LANE_HARD_LIMIT,
    SEAT_FLOOR,
    admission_regime,
    count_by_purpose_class,
    effective_abs_hard,
    escalation_lane_refusal,
    evaluate_new_admission,
    is_seat_purpose,
    purpose_lane_refusal,
)


def _row(purpose: str, status: str = "running") -> dict[str, Any]:
    return {"purpose": purpose, "status": status}


def _snap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seat_count, other_count = count_by_purpose_class(rows)
    regime = admission_regime(seat_count)
    abs_hard = effective_abs_hard(seat_count)
    total = seat_count + other_count
    return {
        "rows": rows,
        "seat_count": seat_count,
        "other_count": other_count,
        "admission_regime": regime,
        "effective_abs_hard": abs_hard,
        "admission_count": total,
        "free_slots": max(0, abs_hard - total),
        "at_soft_limit": total >= 2,
        "at_hard_limit": total >= abs_hard,
    }


def test_unknown_purpose_fail_closes_as_seat() -> None:
    assert is_seat_purpose(None) is True
    assert is_seat_purpose("") is True
    assert is_seat_purpose("ask") is False


def test_admission_regime_boundary() -> None:
    assert admission_regime(3) == "additive"
    assert admission_regime(2) == "carved"
    assert effective_abs_hard(3) == LANE_HARD_LIMIT + ADVISOR_RESERVE
    assert effective_abs_hard(2) == LANE_HARD_LIMIT


# --- sixteen-state table (carved regime unless noted) ---

_STATE_TABLE: list[tuple[str, str | None, int, int, bool, str | None]] = [
    # incoming_purpose_class, purpose_arg, seat, other, unattended, expected_refusal
    ("seat", "operator-proxy", 1, 0, True, None),  # 1 ADMIT
    ("seat", "operator-proxy", 2, 0, True, None),  # 2 ADMIT (global cap advisory)
    ("seat", "operator-proxy", 2, 1, True, None),  # 3 ADMIT
    ("seat", "operator-proxy", 3, 0, True, None),  # 4 ADMIT
    ("advisor", "ask", 0, 0, True, None),  # 5
    ("advisor", "ask", 2, 0, True, None),  # 6 ADMIT
    ("advisor", "ask", 2, 1, True, None),  # 7 ADMIT
    ("advisor", "ask", 3, 0, True, None),  # 8 ADMIT
    ("advisor", "ask", 1, 1, True, None),  # 9 ADMIT (soft advisory)
    ("any", "ask", 1, 2, True, None),  # 10 ADMIT
    ("hop", None, 2, 0, True, None),  # 11 hop ADMIT
    ("hop", None, 3, 0, True, None),  # 12 hop ADMIT
    ("unknown", None, 1, 0, True, None),  # 14 fail-closed seat, room
    ("advisor_soft_bypass", "ask", 2, 0, True, None),  # 15
    ("advisor_soft_held", "ask", 1, 1, True, None),  # 16 ADMIT
]


@pytest.mark.parametrize(
    "label,purpose,seat,other,unattended,expected",
    _STATE_TABLE,
    ids=[row[0] for row in _STATE_TABLE],
)
def test_state_table_row(
    label: str,
    purpose: str | None,
    seat: int,
    other: int,
    unattended: bool,
    expected: str | None,
) -> None:
    hop = label == "hop"
    admit, refusal = evaluate_new_admission(
        purpose,
        seat_count=seat,
        other_count=other,
        unattended=unattended,
        hop_succession=hop,
    )
    if expected is None:
        assert admit is True
        assert refusal is None
    else:
        assert admit is False
        assert refusal == expected


def test_row8_additive_only_changes_verdict() -> None:
    """Global hard ceiling is advisory — carved and additive both admit."""
    steady_admit, steady_ref = evaluate_new_admission(
        "ask", seat_count=2, other_count=1, unattended=True
    )
    assert steady_admit is True
    assert steady_ref is None
    assert admission_regime(3) == "additive"
    additive_admit, additive_ref = evaluate_new_admission(
        "ask", seat_count=3, other_count=0, unattended=True
    )
    assert additive_admit is True
    assert additive_ref is None


def test_operator_proxy_never_consumes_reserve() -> None:
    """Global seat stream cap is advisory — carved regime still admits."""
    admit, label = evaluate_new_admission(
        "operator-proxy", seat_count=2, other_count=0, unattended=True
    )
    assert admit is True
    assert label is None


def test_boundary_walk_occupancy_convergence() -> None:
    """Walk occupancy — global limits advisory; all evaluate paths admit."""
    steps: list[tuple[int, int, bool, str]] = []

    def _probe(seat: int, other: int) -> tuple[bool, str | None]:
        admit, label = evaluate_new_admission(
            "ask", seat_count=seat, other_count=other, unattended=True
        )
        regime = admission_regime(seat)
        steps.append((seat, other, admit, regime))
        return admit, label

    admit, label = _probe(3, 0)
    assert admit is True
    assert admission_regime(3) == "additive"

    admit, label = _probe(3, 1)
    assert admit is True
    assert label is None

    admit, label = _probe(2, 1)
    assert admission_regime(2) == "carved"
    assert admit is True
    assert label is None

    admit, label = _probe(2, 0)
    assert admission_regime(2) == "carved"
    assert admit is True

    seat_admit, seat_label = evaluate_new_admission(
        "operator-proxy", seat_count=2, other_count=0, unattended=True
    )
    assert seat_admit is True
    assert seat_label is None
    adv_admit, _ = evaluate_new_admission(
        "ask", seat_count=2, other_count=0, unattended=True
    )
    assert adv_admit is True

    # No discontinuity: at boundary seat=2 carved vs seat=3 additive
    at_boundary_carved, _ = evaluate_new_admission(
        "ask", seat_count=2, other_count=0, unattended=True
    )
    at_boundary_additive, _ = evaluate_new_admission(
        "ask", seat_count=3, other_count=0, unattended=True
    )
    assert at_boundary_carved is True
    assert at_boundary_additive is True


def test_escalation_lane_refusal_grandfather_unblocks() -> None:
    """Escalation path admits advisor when 3 OP seats hold additive regime."""
    rows = [_row("operator-proxy") for _ in range(3)]
    snap = _snap(rows)
    refuse, label = escalation_lane_refusal(snap, unattended=True, purpose="ask")
    assert refuse is False
    assert label is None


def test_escalation_lane_refusal_carved_hard_full() -> None:
    rows = [_row("operator-proxy") for _ in range(2)] + [_row("ask")]
    snap = _snap(rows)
    refuse, label = escalation_lane_refusal(snap, unattended=True, purpose="ask")
    assert refuse is False
    assert label is None


def test_purpose_lane_refusal_global_seats_advisory() -> None:
    rows = [_row("operator-proxy") for _ in range(2)]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap, purpose="operator-proxy", unattended=True
    )
    assert refuse is False
    assert label is None


def test_purpose_lane_refusal_wires_hop_succession() -> None:
    rows = [_row("operator-proxy") for _ in range(2)]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap, purpose="operator-proxy", unattended=True, hop_succession=True
    )
    assert refuse is False
    assert label is None


def test_same_lane_seat_cap_refuses_second_holder() -> None:
    rows = [
        {**_row("operator-proxy"), "parent_thread": "6655", "execution_id": "a"},
    ]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap, purpose="operator-proxy", parent_thread="6655"
    )
    assert refuse is True
    assert label == "seat_cap"


def test_same_lane_hop_overlap_admits_one_successor() -> None:
    rows = [
        {**_row("operator-proxy"), "parent_thread": "6655", "execution_id": "a"},
    ]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap,
        purpose="operator-proxy",
        parent_thread="6655",
        hop_succession=True,
    )
    assert refuse is False
    assert label is None


def test_same_lane_hop_refuses_third() -> None:
    rows = [
        {**_row("operator-proxy"), "parent_thread": "6655", "execution_id": "a"},
        {**_row("operator-proxy"), "parent_thread": "6655", "execution_id": "b"},
    ]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap,
        purpose="operator-proxy",
        parent_thread="6655",
        hop_succession=True,
    )
    assert refuse is True
    assert label == "seat_cap"


def test_unbound_legacy_row_skips_same_lane_cap() -> None:
    rows = [_row("operator-proxy")]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(
        snap, purpose="operator-proxy", parent_thread="6655"
    )
    assert refuse is False
    assert label is None


def test_effective_abs_hard_raises_by_exactly_reserve() -> None:
    assert effective_abs_hard(3) - LANE_HARD_LIMIT == ADVISOR_RESERVE
    assert effective_abs_hard(2) == LANE_HARD_LIMIT


def test_count_by_purpose_class_from_rows() -> None:
    rows = [
        _row("operator-proxy"),
        _row("mission"),
        _row("ask"),
        _row("escalation"),
        {"purpose": "operator-proxy", "status": "completed"},
    ]
    seat, other = count_by_purpose_class(rows)
    assert seat == 2
    assert other == 2


def test_dual_advisor_no_seat_admits() -> None:
    admit, label = evaluate_new_admission(
        "ask", seat_count=0, other_count=1, unattended=True
    )
    assert admit is True
    assert label is None


def test_third_advisor_no_seat_admits() -> None:
    admit, label = evaluate_new_admission(
        "ask", seat_count=0, other_count=2, unattended=True
    )
    assert admit is True
    assert label is None


def test_attended_seat_plus_advisor_admits_second_advisor() -> None:
    admit, label = evaluate_new_admission(
        "ask", seat_count=1, other_count=1, unattended=False
    )
    assert admit is True
    assert label is None


def test_purpose_lane_refusal_advisor_cap_advisory() -> None:
    rows = [_row("ask") for _ in range(2)]
    snap = _snap(rows)
    refuse, label = purpose_lane_refusal(snap, purpose="ask", unattended=True)
    assert refuse is False
    assert label is None


def test_seat_floor_constant() -> None:
    assert ADVISOR_RESERVE == 1
    assert SEAT_FLOOR == 1
