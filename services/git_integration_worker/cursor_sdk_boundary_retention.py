"""Retention boundary — terminal toolcall emit must carry body or explicit absence (item 17)."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryContractError,
    BoundaryEmitResult,
    BoundaryShapeViolation,
    register_boundary_contract,
    validate_at_emit,
)
from services.git_integration_worker.cursor_sdk_toolcall_retention import (
    RESULT_BODY_ABSENT_ERROR,
    RESULT_BODY_ABSENT_NULL,
    RESULT_BODY_ABSENT_OVERSIZED,
    RESULT_BODY_ABSENT_STREAM_TRUNCATED,
    RESULT_BODY_PRESENT,
    ToolcallResultRetention,
)

RETENTION_BOUNDARY = "retention"
_EXPLICIT_ABSENT = frozenset(
    {
        RESULT_BODY_ABSENT_NULL,
        RESULT_BODY_ABSENT_ERROR,
        RESULT_BODY_ABSENT_STREAM_TRUNCATED,
        RESULT_BODY_ABSENT_OVERSIZED,
    }
)


def classify_retention_shape(value: object) -> str:
    """Label retention fields at terminal toolcall emit."""
    if not isinstance(value, ToolcallResultRetention):
        return "non_retention"
    status = value.result_body_status
    if status == RESULT_BODY_PRESENT:
        return "present"
    if status in _EXPLICIT_ABSENT:
        return status
    return "unmarked"


def _validate_retention_emit(value: object, shape_label: str) -> BoundaryShapeViolation | None:
    if shape_label in {RESULT_BODY_PRESENT, *_EXPLICIT_ABSENT}:
        return None
    if isinstance(value, ToolcallResultRetention):
        return BoundaryShapeViolation(
            boundary=RETENTION_BOUNDARY,
            expected="result_body_status in {present, absent_*}",
            arrived=f"status={value.result_body_status!r}",
            detail="metadata-only toolcall row — result_bytes without explicit retention (item-22 class)",
        )
    return BoundaryShapeViolation(
        boundary=RETENTION_BOUNDARY,
        expected="ToolcallResultRetention at terminal emit",
        arrived=f"shape={shape_label}",
        detail="retention contract requires explicit body or absent_* status at emit",
    )


def emit_retention_boundary(
    retention: ToolcallResultRetention,
    *,
    result_bytes: int = 0,
    status: str = "completed",
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate retention payload at terminal toolcall emit."""
    result = validate_at_emit(RETENTION_BOUNDARY, retention, strict=strict)
    if result.violation is not None:
        if strict:
            raise BoundaryContractError(result.violation)
        return result
    if (
        result_bytes > 0
        and str(status).lower() == "completed"
        and retention.result_body_status == RESULT_BODY_PRESENT
        and retention.result_body is None
    ):
        violation = BoundaryShapeViolation(
            boundary=RETENTION_BOUNDARY,
            expected="result_body present when status=present and result_bytes>0",
            arrived=f"status={retention.result_body_status!r} body=null result_bytes={result_bytes}",
            detail="present status without payload — absence reported as zero",
        )
        result = BoundaryEmitResult(
            value=retention, shape_label=result.shape_label, violation=violation
        )
        if strict:
            raise BoundaryContractError(violation)
    return result


def _register() -> None:
    register_boundary_contract(
        RETENTION_BOUNDARY,
        classify=classify_retention_shape,
        validate=_validate_retention_emit,
    )


_register()

__all__ = [
    "RETENTION_BOUNDARY",
    "classify_retention_shape",
    "emit_retention_boundary",
]
