"""Fail-closed typed work-item admit schema (R2 control plane)."""

from __future__ import annotations

from dataclasses import dataclass

_VALID_LANES = frozenset({"mechanical", "judgment", "consult"})
_VALID_ATTENDANCE = frozenset({"attended", "autonomous", "operator_proxy"})

Attendance = str  # attended | autonomous | operator_proxy


@dataclass(frozen=True)
class TypedWorkItemAdmit:
    """Fail-closed typed admit schema — sole authority for standing work."""

    root_id: str
    pickup_gid: str
    pickup_lane: str
    attendance: Attendance
    pickup_executor: str | None = None
    scoreboard_uri: str = ""


class TypedAdmitError(ValueError):
    """Raised when typed admit fields fail validation."""

    def __init__(self, *, detail: str, field: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.field = field
        self.error_code = "typed_admit_invalid"


def validate_typed_admit(admit: TypedWorkItemAdmit) -> None:
    """Fail-closed validation for typed work-item admit (R2)."""
    root_id = str(admit.root_id or "").strip()
    if not root_id:
        raise TypedAdmitError(detail="root_id is required", field="root_id")
    pickup_gid = str(admit.pickup_gid or "").strip()
    if not pickup_gid:
        raise TypedAdmitError(detail="pickup_gid is required", field="pickup_gid")
    lane = str(admit.pickup_lane or "").strip().lower()
    if lane not in _VALID_LANES:
        raise TypedAdmitError(
            detail=f"pickup_lane must be one of {sorted(_VALID_LANES)}",
            field="pickup_lane",
        )
    attendance = str(admit.attendance or "").strip().lower()
    if attendance not in _VALID_ATTENDANCE:
        raise TypedAdmitError(
            detail=f"attendance must be one of {sorted(_VALID_ATTENDANCE)}",
            field="attendance",
        )


def typed_record_valid(row: object | None) -> bool:
    """True when the ledger row carries a complete typed admit surface."""
    if row is None:
        return False
    root_id = str(getattr(row, "root_id", "") or "").strip()
    pickup_gid = str(getattr(row, "pickup_gid", "") or "").strip()
    pickup_lane = str(getattr(row, "pickup_lane", "") or "").strip().lower()
    attendance = str(getattr(row, "attendance", "") or "").strip().lower()
    scoreboard_uri = str(getattr(row, "scoreboard_uri", "") or "").strip()
    return bool(
        root_id
        and pickup_gid
        and pickup_lane in _VALID_LANES
        and attendance in _VALID_ATTENDANCE
        and scoreboard_uri
    )


__all__ = [
    "Attendance",
    "TypedAdmitError",
    "TypedWorkItemAdmit",
    "typed_record_valid",
    "validate_typed_admit",
]
