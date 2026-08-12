"""Purpose-aware CDP lane admission — Option A with transitional additive regime.

When ``seat_count > (LANE_HARD_LIMIT - ADVISOR_RESERVE)`` the advisor reservation
is **additive** (effective absolute hard raised by exactly ``ADVISOR_RESERVE`` for
non-seat admits only). Once occupancy falls to or below that line the reservation
is **carved** from the existing hard limit (+0 steady-state cost).
"""

from __future__ import annotations

from typing import Any

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES

LANE_SOFT_LIMIT = 2
LANE_HARD_LIMIT = 3
ADVISOR_RESERVE = 1
SEAT_HARD = LANE_HARD_LIMIT - ADVISOR_RESERVE

SEAT_PURPOSES = OPERATOR_PROXY_MISSION_PURPOSES

AdmissionRefusal = str  # seat_cap | abs_hard | soft | hard


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
    """Return ``(admit, refusal_label)`` for one new CDP stream admission."""
    total = seat_count + other_count

    if hop_succession:
        if total >= LANE_HARD_LIMIT:
            return False, "hard"
        if LANE_HARD_LIMIT - total < 1:
            return False, "hard"
        return True, None

    regime = admission_regime(seat_count)
    abs_hard = effective_abs_hard(seat_count)
    incoming_seat = is_seat_purpose(incoming_purpose)

    if total >= abs_hard:
        return False, "abs_hard"

    if incoming_seat:
        if regime == "carved" and seat_count >= SEAT_HARD:
            return False, "seat_cap"
        if regime == "additive" and seat_count >= LANE_HARD_LIMIT:
            return False, "abs_hard"
        return True, None

    # Advisor / escalation — never operator-proxy (handled above).
    if regime == "additive":
        return True, None

    if other_count >= ADVISOR_RESERVE:
        if unattended and total >= LANE_SOFT_LIMIT:
            return False, "soft"
        return False, "abs_hard"

    if seat_count >= SEAT_HARD and other_count < ADVISOR_RESERVE:
        return True, None

    if unattended and total >= LANE_SOFT_LIMIT:
        return False, "soft"

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


def purpose_lane_refusal(
    snap: dict[str, Any],
    *,
    purpose: str | None,
    unattended: bool = True,
) -> tuple[bool, AdmissionRefusal | None]:
    """General submit-path gate keyed on incoming purpose."""
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
