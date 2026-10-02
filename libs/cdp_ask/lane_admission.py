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

import contextlib
from typing import Any, Literal

from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

# Advisory stream telemetry only. Cowork does not cap concurrent sessions at a
# small integer; 2/3 read as "full" while the product still had room. The
# ceilings stay finite so ``free_slots`` remains an int. They do not refuse mint.
LANE_SOFT_LIMIT = 32
LANE_HARD_LIMIT = 64
ADMISSION_COUNT_SCOPE = (
    f"running/stream admissions, this host (soft={LANE_SOFT_LIMIT} hard={LANE_HARD_LIMIT})"
)
ADVISOR_RESERVE = 1
SEAT_FLOOR = 1

SEAT_PURPOSES = OPERATOR_PROXY_MISSION_PURPOSES

AdmissionRefusal = str  # seat_cap | abs_hard | soft | hard | advisor_cap | operator_seat_held | …

SeatHolderState = Literal["held", "vacant", "conflict", "unavailable"]


def _seat_bound_at_float(raw: Any) -> float:
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def lane_seat_holder(
    snap: dict[str, Any],
    lane: str,
    *,
    purposes: frozenset[str] = OPERATOR_PURPOSES,
) -> dict[str, Any]:
    """Fold ``seat_rows`` into one live operator identity for *lane*."""
    lane_key = (lane or "").strip()
    observed_at = str(snap.get("observed_at") or "").strip() or None
    seat_rows = snap.get("seat_rows")
    if not isinstance(seat_rows, list):
        return {
            "state": "unavailable",
            "registration_id": None,
            "chat_url": None,
            "seat_bound_at": None,
            "source": None,
            "observed_at": observed_at,
            "candidates": None,
        }
    candidates: list[tuple[str, float, dict[str, Any]]] = []
    for row in seat_rows:
        if not isinstance(row, dict):
            continue
        purpose = str(row.get("purpose") or "").strip().lower()
        if purpose not in purposes:
            continue
        if str(row.get("parent_thread") or "").strip() != lane_key:
            continue
        reg_id = str(row.get("registration_id") or "").strip()
        if not reg_id:
            continue
        bound = _seat_bound_at_float(row.get("seat_bound_at"))
        candidates.append((reg_id, bound, row))
    if not candidates:
        return {
            "state": "vacant",
            "registration_id": None,
            "chat_url": None,
            "seat_bound_at": None,
            "source": str(snap.get("source") or "active-work"),
            "observed_at": observed_at,
            "candidates": None,
        }
    if len(candidates) == 1:
        reg_id, bound, row = candidates[0]
        return {
            "state": "held",
            "registration_id": reg_id,
            "chat_url": row.get("chat_url"),
            "seat_bound_at": bound,
            "source": str(row.get("source") or "active-work"),
            "observed_at": observed_at,
            "candidates": None,
        }
    winner = max(candidates, key=lambda item: item[1])
    candidate_ids = [item[0] for item in candidates]
    from claude_bundles import cdp_registry_events as _events

    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_seat_lane_conflict(
                lane=lane_key,
                candidate_ids=candidate_ids,
                observed_at=observed_at or "",
            )
        )
    _reg_id, bound, row = winner
    return {
        "state": "conflict",
        "registration_id": _reg_id,
        "chat_url": row.get("chat_url"),
        "seat_bound_at": bound,
        "source": str(row.get("source") or "active-work"),
        "observed_at": observed_at,
        "candidates": candidate_ids,
    }


def _operator_seat_held_envelope(
    *,
    lane: str,
    holder: dict[str, Any],
) -> dict[str, Any]:
    reg = str(holder.get("registration_id") or "").strip()
    lane_s = lane.strip()
    return {
        "code": "operator_seat_held",
        "message": (
            f"operator seat already held on lane {lane_s!r}; "
            "use cse_session followup into the live operator"
        ),
        "source": "cdp_ask",
        "retryable": False,
        "data": {
            "lane": lane_s,
            "holder_registration_id": reg,
            "holder_chat_url": holder.get("chat_url"),
            "seat_bound_at": holder.get("seat_bound_at"),
            "observed_at": holder.get("observed_at"),
            "next": f"cse_session op=followup parent_thread={lane_s}",
        },
    }


def seat_holder_refusal(
    snap: dict[str, Any],
    *,
    lane: str,
    purpose: str | None,
    mission_kind: str | None = None,
    predecessor_registration_id: str | None = None,
    hop_succession: bool = False,
) -> dict[str, Any] | None:
    """Refuse non-hop operator admits when the lane seat journal reports a holder."""
    if not is_seat_purpose(purpose):
        return None
    lane_key = (lane or "").strip()
    if not lane_key:
        return None
    if "seat_rows" not in snap:
        return None
    holder = lane_seat_holder(snap, lane_key)
    state: SeatHolderState = holder["state"]
    is_hop = hop_succession or (mission_kind or "").strip().lower() == "hop"
    if state == "unavailable":
        return {
            "code": "seat_unavailable",
            "message": "seat-axis projection unavailable for lane admission",
            "source": "cdp_ask",
            "retryable": True,
            "data": {"lane": lane_key},
        }
    if is_hop:
        if state in {"held", "conflict"}:
            pred = (predecessor_registration_id or "").strip()
            incumbent = str(holder.get("registration_id") or "").strip()
            if pred and incumbent and pred != incumbent:
                return {
                    "code": "seat_holder_mismatch",
                    "message": (
                        "hop predecessor_registration_id does not match lane holder"
                    ),
                    "source": "cdp_ask",
                    "retryable": False,
                    "data": {
                        "lane": lane_key,
                        "holder_registration_id": incumbent,
                        "predecessor_registration_id": pred,
                    },
                }
        return None
    if state in {"held", "conflict"}:
        return _operator_seat_held_envelope(lane=lane_key, holder=holder)
    return None


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
    mission_kind: str | None = None,
    predecessor_registration_id: str | None = None,
) -> tuple[bool, AdmissionRefusal | None, dict[str, Any] | None]:
    """General submit-path gate keyed on incoming purpose and seat binding.

    Same-lane uniqueness: one operator holder per ``parent_thread``, plus one
    hop-succession overlap. Unbound incoming (no ``parent_thread``) skips the
    per-lane cap — missing binding is INDETERMINATE, not a silent refuse.
    """
    rows = snap.get("rows") or []
    lane = str(parent_thread or "").strip()
    if lane and is_seat_purpose(purpose):
        seat_refusal = seat_holder_refusal(
            snap,
            lane=lane,
            purpose=purpose,
            mission_kind=mission_kind,
            predecessor_registration_id=predecessor_registration_id,
            hop_succession=hop_succession,
        )
        if seat_refusal is not None:
            code = str(seat_refusal.get("code") or "operator_seat_held")
            return True, code, seat_refusal
    if lane and is_seat_purpose(purpose):
        same = count_seats_on_lane(rows, lane)
        if hop_succession:
            if same >= 2:
                return True, "seat_cap", None
        elif same >= 1:
            return True, "seat_cap", None
    seat_count, other_count = count_by_purpose_class(rows)
    admit, label = evaluate_new_admission(
        purpose,
        seat_count=seat_count,
        other_count=other_count,
        unattended=unattended,
        hop_succession=hop_succession,
    )
    if admit:
        return False, None, None
    if label == "abs_hard":
        return True, "hard", None
    return True, label, None
