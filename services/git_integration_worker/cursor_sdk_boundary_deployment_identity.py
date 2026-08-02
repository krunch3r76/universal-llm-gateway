"""Deployment-identity boundary — verification must probe the executing process (item 17)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryEmitResult,
    BoundaryShapeViolation,
    register_boundary_contract,
    validate_at_emit,
    _summarize_value,
)
from services.git_integration_worker.cursor_auto.propagation_probe import process_identity

DEPLOYMENT_IDENTITY_BOUNDARY = "deployment_identity"


@dataclass(frozen=True)
class DeploymentIdentityEmit:
    """Value crossing the deployment-identity boundary at verification emit."""

    expected_executor: str
    probed_surface: str
    payload: dict[str, Any] | None
    code_ref: str | None = None
    before_payload: dict[str, Any] | None = None
    landed_at_monotonic: float | None = None


def classify_deployment_identity_shape(value: object) -> str:
    if not isinstance(value, DeploymentIdentityEmit):
        return "malformed"
    if value.payload is None:
        return "probe_unreachable"
    if value.expected_executor != value.probed_surface:
        return "surface_mismatch"
    before = value.before_payload
    after = value.payload
    if before is not None:
        before_id = process_identity(before)
        after_id = process_identity(after)
        if before_id and after_id and before_id == after_id:
            return "stale_process_same_pid"
    return "identity_ok"


def _validate_deployment_identity_emit(
    value: object,
    shape_label: str,
) -> BoundaryShapeViolation | None:
    if shape_label == "identity_ok":
        return None
    if not isinstance(value, DeploymentIdentityEmit):
        return BoundaryShapeViolation(
            boundary=DEPLOYMENT_IDENTITY_BOUNDARY,
            expected="DeploymentIdentityEmit at verification emit",
            arrived=f"shape={shape_label}",
            detail="malformed deployment identity crossing",
        )
    emit = value
    if shape_label == "probe_unreachable":
        return BoundaryShapeViolation(
            boundary=DEPLOYMENT_IDENTITY_BOUNDARY,
            expected=f"live probe of executor {emit.expected_executor!r}",
            arrived="probe_unreachable",
            detail="verification cannot reach probed process",
        )
    if shape_label == "surface_mismatch":
        return BoundaryShapeViolation(
            boundary=DEPLOYMENT_IDENTITY_BOUNDARY,
            expected=f"probe targets executor {emit.expected_executor!r}",
            arrived=f"probed_surface={emit.probed_surface!r} payload={_summarize_value(emit.payload)}",
            detail=(
                "wrong process answered — relay probed while executor runs stale code "
                "(reattach: MCP container green, cdp-ask satellite never restarted)"
            ),
        )
    if shape_label == "stale_process_same_pid":
        before_id = process_identity(emit.before_payload or {})
        after_id = process_identity(emit.payload or {})
        return BoundaryShapeViolation(
            boundary=DEPLOYMENT_IDENTITY_BOUNDARY,
            expected="process identity change after deploy/restart",
            arrived=f"before={before_id} after={after_id} code_ref={emit.code_ref!r}",
            detail=(
                "green verification against process running pre-land code — "
                "code_version match without pid change (reattach falsifier class)"
            ),
        )
    return None


def emit_deployment_identity_boundary(
    emit: DeploymentIdentityEmit,
    *,
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate deployment identity at verification emit."""
    return validate_at_emit(DEPLOYMENT_IDENTITY_BOUNDARY, emit, strict=strict)


def _register() -> None:
    register_boundary_contract(
        DEPLOYMENT_IDENTITY_BOUNDARY,
        classify=classify_deployment_identity_shape,
        validate=_validate_deployment_identity_emit,
    )


_register()

__all__ = [
    "DEPLOYMENT_IDENTITY_BOUNDARY",
    "DeploymentIdentityEmit",
    "classify_deployment_identity_shape",
    "emit_deployment_identity_boundary",
]
