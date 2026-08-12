"""Typed predecessor handle for hop-cadence succession confirm (arc 7119 R10).

Separates genuine first-seat-on-lane (explicit sentinel) from lookup failure
( loud error ) from incumbent recorded (both registration and execution ids).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

# Explicit sentinels — empty string must never mean "unknown which case".
PRIOR_NONE_REGISTRATION = "__none:first_seat_on_lane__"
PRIOR_NONE_EXECUTION = "__none:no_incumbent_execution__"


class PredecessorVerdict(str, Enum):
    """Verdict for predecessor resolution at hop fire or confirm."""

    INCUMBENT_RECORDED = "incumbent_recorded"
    FIRST_SEAT_ON_LANE = "first_seat_on_lane"
    LOOKUP_FAILED = "lookup_failed"


@dataclass(frozen=True)
class PredecessorHandle:
    """Non-ambiguous predecessor ids persisted on the watch ledger."""

    registration_id: str
    execution_id: str
    verdict: PredecessorVerdict
    absence_reason: str | None = None

    def as_watch_fields(self) -> dict[str, Any]:
        """Persisted watch-row keys for later confirm readers."""
        out: dict[str, Any] = {
            "superseded_registration_id": self.registration_id,
            "superseded_execution_id": self.execution_id,
            "predecessor_verdict": self.verdict.value,
        }
        if self.absence_reason:
            out["predecessor_absence_reason"] = self.absence_reason
        return out


class PredecessorConfirmError(Exception):
    """Confirm path cannot resolve a predecessor handle that should exist."""

    def __init__(self, *, thread_id: str, reason: str, detail: dict[str, Any]):
        super().__init__(reason)
        self.thread_id = thread_id
        self.reason = reason
        self.detail = detail


def execution_id_for_registration(
    snap: dict[str, Any],
    registration_id: str,
) -> str | None:
    """Return running/pending execution id for a live registration in a snapshot."""
    reg = registration_id.strip()
    if not reg:
        return None
    rows = snap.get("rows") if isinstance(snap.get("rows"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        row_reg = str(row.get("registration_id") or "").strip()
        exec_id = str(row.get("execution_id") or "").strip()
        if row_reg == reg and exec_id and status in {"pending", "running"}:
            return exec_id
    return None


def capture_predecessor_at_hop(
    row: dict[str, Any],
    snap: dict[str, Any] | None,
) -> PredecessorHandle | PredecessorConfirmError:
    """Resolve predecessor handle at hop fire; fail closed when lookup should succeed."""
    predecessor_reg = str(row.get("registration_id") or "").strip()
    if not predecessor_reg:
        return PredecessorHandle(
            registration_id=PRIOR_NONE_REGISTRATION,
            execution_id=PRIOR_NONE_EXECUTION,
            verdict=PredecessorVerdict.FIRST_SEAT_ON_LANE,
            absence_reason="no_registration_id_on_watch_at_hop_fire",
        )
    snap_dict = snap if isinstance(snap, dict) else {}
    exec_id = execution_id_for_registration(snap_dict, predecessor_reg)
    if exec_id:
        return PredecessorHandle(
            registration_id=predecessor_reg,
            execution_id=exec_id,
            verdict=PredecessorVerdict.INCUMBENT_RECORDED,
        )
    thread_id = str(row.get("thread_id") or "")
    logger.error(
        "hop_cadence predecessor lookup failed thread=%s reg=%s",
        thread_id,
        predecessor_reg,
    )
    return PredecessorConfirmError(
        thread_id=thread_id,
        reason="predecessor_execution_lookup_failed",
        detail={
            "registration_id": predecessor_reg,
            "verdict": PredecessorVerdict.LOOKUP_FAILED.value,
        },
    )


def _registration_for_snap_fallback(
    row: dict[str, Any],
    error: PredecessorConfirmError,
) -> str | None:
    """Registration id to consult in active_work when the watch row lacks execution id."""
    for key in ("superseded_registration_id", "registration_id"):
        val = str(error.detail.get(key) or "").strip()
        if val and val != PRIOR_NONE_REGISTRATION:
            return val
    for key in ("superseded_registration_id", "registration_id"):
        val = str(row.get(key) or "").strip()
        if val and val != PRIOR_NONE_REGISTRATION:
            return val
    return None


def predecessor_for_confirm(
    row: dict[str, Any],
    active_work_snap: dict[str, Any] | None = None,
) -> PredecessorHandle | PredecessorConfirmError:
    """Resolve predecessor at confirm; consult active_work when watch lacks execution id.

    Hop fire persists execution ids from ``cdp_ask.active_work`` (R10). Legacy watch
    rows may carry registration without ``superseded_execution_id``; confirm must read
    the same snapshot rather than fail every tick on stale ledger fields.
    """
    handle = predecessor_from_watch(row)
    if not isinstance(handle, PredecessorConfirmError):
        return handle
    if handle.reason not in {
        "incumbent_registration_without_execution_id",
        "incumbent_handle_incomplete",
    }:
        return handle
    reg = _registration_for_snap_fallback(row, handle)
    if not reg:
        return handle
    snap_dict = active_work_snap if isinstance(active_work_snap, dict) else {}
    exec_id = execution_id_for_registration(snap_dict, reg)
    if not exec_id:
        return handle
    return PredecessorHandle(
        registration_id=reg,
        execution_id=exec_id,
        verdict=PredecessorVerdict.INCUMBENT_RECORDED,
    )


def predecessor_from_watch(row: dict[str, Any]) -> PredecessorHandle | PredecessorConfirmError:
    """Load persisted predecessor handle for confirm; fail loud on corrupt state."""
    thread_id = str(row.get("thread_id") or "")
    verdict_raw = str(row.get("predecessor_verdict") or "").strip()
    reg = str(row.get("superseded_registration_id") or "").strip()
    exec_id = str(row.get("superseded_execution_id") or "").strip()

    if verdict_raw == PredecessorVerdict.FIRST_SEAT_ON_LANE.value:
        if reg and reg != PRIOR_NONE_REGISTRATION:
            return PredecessorConfirmError(
                thread_id=thread_id,
                reason="first_seat_sentinel_mismatch",
                detail={"superseded_registration_id": reg},
            )
        return PredecessorHandle(
            registration_id=reg or PRIOR_NONE_REGISTRATION,
            execution_id=exec_id or PRIOR_NONE_EXECUTION,
            verdict=PredecessorVerdict.FIRST_SEAT_ON_LANE,
            absence_reason=str(row.get("predecessor_absence_reason") or "") or None,
        )

    if verdict_raw == PredecessorVerdict.INCUMBENT_RECORDED.value:
        if not reg or not exec_id:
            return PredecessorConfirmError(
                thread_id=thread_id,
                reason="incumbent_handle_incomplete",
                detail={
                    "superseded_registration_id": reg,
                    "superseded_execution_id": exec_id,
                },
            )
        return PredecessorHandle(
            registration_id=reg,
            execution_id=exec_id,
            verdict=PredecessorVerdict.INCUMBENT_RECORDED,
        )

    # Legacy rows: infer from superseded_registration_id if present at hop time.
    hop_reg = str(row.get("registration_id") or "").strip()
    legacy_superseded = str(row.get("superseded_registration_id") or "").strip()
    if legacy_superseded and exec_id:
        return PredecessorHandle(
            registration_id=legacy_superseded,
            execution_id=exec_id,
            verdict=PredecessorVerdict.INCUMBENT_RECORDED,
        )
    if legacy_superseded and not exec_id:
        return PredecessorConfirmError(
            thread_id=thread_id,
            reason="incumbent_registration_without_execution_id",
            detail={"superseded_registration_id": legacy_superseded},
        )
    if hop_reg:
        return PredecessorConfirmError(
            thread_id=thread_id,
            reason="incumbent_registration_without_execution_id",
            detail={"registration_id": hop_reg},
        )
    return PredecessorHandle(
        registration_id=PRIOR_NONE_REGISTRATION,
        execution_id=PRIOR_NONE_EXECUTION,
        verdict=PredecessorVerdict.FIRST_SEAT_ON_LANE,
        absence_reason="legacy_row_no_predecessor_fields",
    )


def prior_registration_for_confirm(handle: PredecessorHandle) -> str:
    """Registration id emitted as prior_registration_id on confirm observables."""
    return handle.registration_id


__all__ = [
    "PRIOR_NONE_EXECUTION",
    "PRIOR_NONE_REGISTRATION",
    "PredecessorConfirmError",
    "PredecessorHandle",
    "PredecessorVerdict",
    "capture_predecessor_at_hop",
    "execution_id_for_registration",
    "predecessor_for_confirm",
    "predecessor_from_watch",
    "prior_registration_for_confirm",
]
