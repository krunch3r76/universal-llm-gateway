"""Purpose-aware CDP lane admission — class ceilings with transitional additive regime.

``ADVISOR_RESERVE`` is the first-advisor floor that seats cannot consume. Additional
advisors share stream limits up to ``LANE_HARD_LIMIT - SEAT_FLOOR``. When
``seat_count > (LANE_HARD_LIMIT - ADVISOR_RESERVE)`` the reservation is **additive**
(effective absolute hard raised by exactly ``ADVISOR_RESERVE`` for non-seat admits
only). Once occupancy falls to or below that line the reservation is **carved** from
the existing hard limit (+0 steady-state cost).

``LANE_SOFT_LIMIT`` and ``LANE_HARD_LIMIT`` remain **advisory** in
``work_projection`` (``at_soft_limit``, ``at_hard_limit``, ``free_slots``). They
do not refuse submit-path or hop-cadence admission (operator directive 2026-09-01).
Per-lane seat uniqueness is enforced in ``purpose_lane_refusal`` before global
count evaluation.
"""

from __future__ import annotations

from typing import Any

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES

LANE_SOFT_LIMIT = 2
LANE_HARD_LIMIT = 3
ADVISOR_RESERVE = 1
SEAT_FLOOR = 1

SEAT_PURPOSES = OPERATOR_PROXY_MISSION_PURPOSES

AdmissionRefusal = str  # seat_cap | abs_hard | soft | hard | advisor_cap


def is_seat_purpose(purpose: str | None) -> bool:
    """True for operator-proxy/mission; unknown/missing fail-closes as seat."""
    if purpose is None or not str(purpose).strip():
        return True
    return str(purpose).strip().lower() in SEAT_PURPOSES


def count_by_purpose_class(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Return ``(seat_count, other_count)`` for pending/running rows."""
    seat_count = 0
    other_count = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {"pending", "running"}:
            continue
        if is_seat_purpose(row.get("purpose")):
            seat_count += 1
        else:
            other_count += 1
    return seat_count, other_count


def admission_regime(seat_count: int) -> str:
    """``additive`` while over the carve line; ``carved`` at or below it."""
    if seat_count > LANE_HARD_LIMIT - ADVISOR_RESERVE:
        return "additive"
    return "carved"


def effective_abs_hard(seat_count: int) -> int:
    """Absolute concurrent-stream ceiling for the current regime."""
    if admission_regime(seat_count) == "additive":
        return LANE_HARD_LIMIT + ADVISOR_RESERVE
    return LANE_HARD_LIMIT


def evaluate_new_admission(
    incoming_purpose: str | None,
    *,
    seat_count: int,
    other_count: int,
    unattended: bool = True,
    hop_succession: bool = False,
) -> tuple[bool, AdmissionRefusal | None]:
    """Return ``(admit, refusal_label)`` for one new CDP stream admission.

    Global stream-count ceilings are advisory only — see module docstring.
    Per-lane seat caps are enforced in ``purpose_lane_refusal`` before this runs.
    """
    _ = (
        incoming_purpose,
        seat_count,
        other_count,
        unattended,
        hop_succession,
    )
    return True, None


def escalation_lane_refusal(
    snap: dict[str, Any],
    *,
    unattended: bool,
    purpose: str | None = "ask",
) -> tuple[bool, AdmissionRefusal | None]:
    """Purpose-aware escalation gate (advisor/escalation admits only)."""
    rows = snap.get("rows") or []
    seat_count, other_count = count_by_purpose_class(rows)
    admit, label = evaluate_new_admission(
        purpose,
        seat_count=seat_count,
        other_count=other_count,
        unattended=unattended,
    )
    if admit:
        return False, None
    if label == "abs_hard":
        return True, "hard"
    return True, label


def count_seats_on_lane(rows: list[dict[str, Any]], parent_thread: str) -> int:
    """Count pending/running operator-purpose rows bound to ``parent_thread``.

    Rows without ``parent_thread`` are unbound — they do not join a lane and
    must not be treated as holders (INDETERMINATE, not a refuse).
    """
    lane = (parent_thread or "").strip()
    if not lane:
        return 0
    count = 0
    for row in rows:
        status = str(row.get("status") or "")
        if status not in {"pending", "running"}:
            continue
        if not is_seat_purpose(row.get("purpose")):
            continue
        row_lane = str(row.get("parent_thread") or "").strip()
        if row_lane == lane:
            count += 1
    return count


def purpose_lane_refusal(
    snap: dict[str, Any],
    *,
    purpose: str | None,
    unattended: bool = True,
    hop_succession: bool = False,
    parent_thread: str | None = None,
) -> tuple[bool, AdmissionRefusal | None]:
    """General submit-path gate keyed on incoming purpose and seat binding.

    Same-lane uniqueness: one operator holder per ``parent_thread``, plus one
    hop-succession overlap. Unbound incoming (no ``parent_thread``) skips the
    per-lane cap — missing binding is INDETERMINATE, not a silent refuse.
    """
    rows = snap.get("rows") or []
    lane = str(parent_thread or "").strip()
    if lane and is_seat_purpose(purpose):
        same = count_seats_on_lane(rows, lane)
        if hop_succession:
            if same >= 2:
                return True, "seat_cap"
        elif same >= 1:
            return True, "seat_cap"
    seat_count, other_count = count_by_purpose_class(rows)
    admit, label = evaluate_new_admission(
        purpose,
        seat_count=seat_count,
        other_count=other_count,
        unattended=unattended,
        hop_succession=hop_succession,
    )
    if admit:
        return False, None
    if label == "abs_hard":
        return True, "hard"
    return True, label
