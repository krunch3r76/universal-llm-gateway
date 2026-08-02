"""Name-gate boundary — MCP wire ``mcp`` must resolve to logical ``toolName`` (item 17)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryEmitResult,
    BoundaryShapeViolation,
    register_boundary_contract,
    validate_at_emit,
    _summarize_value,
)

NAME_GATE_BOUNDARY = "name_gate"
_MCP_WIRE = "mcp"


def classify_name_gate_shape(value: object) -> str:
    """Label a (wire_name, args) pair at the stream name-gate emit point."""
    if not isinstance(value, tuple) or len(value) != 2:
        return "malformed"
    wire_name, args = value
    wire = str(wire_name or "").strip().lower()
    if wire != _MCP_WIRE:
        return "passthrough"
    if not isinstance(args, Mapping):
        return "mcp_no_args"
    logical = args.get("toolName") or args.get("tool_name")
    if isinstance(logical, str) and logical.strip():
        return "mcp_with_logical"
    return "mcp_unresolved"


def _validate_name_gate_emit(value: object, shape_label: str) -> BoundaryShapeViolation | None:
    if shape_label != "mcp_with_logical":
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        return None
    wire_name, args = value
    if not isinstance(args, Mapping):
        return None
    logical = args.get("toolName") or args.get("tool_name")
    resolved = str(logical).strip() if isinstance(logical, str) else ""
    if resolved and str(wire_name or "").strip().lower() == _MCP_WIRE:
        return None
    return BoundaryShapeViolation(
        boundary=NAME_GATE_BOUNDARY,
        expected=f"logical toolName={resolved!r} not wire name {_MCP_WIRE!r}",
        arrived=f"wire={wire_name!r} args={_summarize_value(args)}",
        detail="production stream leaves tool_name=mcp — reconcile/harvest gates miss cortex writes",
    )


def emit_name_gate_boundary(
    wire_name: str,
    args: Any,
    *,
    resolved_name: str,
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate resolved stream tool name at the name-gate emit point."""
    result = validate_at_emit(NAME_GATE_BOUNDARY, (wire_name, args), strict=strict)
    if result.violation is not None:
        return result
    shape = result.shape_label
    if shape == "mcp_with_logical" and resolved_name.lower() == _MCP_WIRE:
        violation = BoundaryShapeViolation(
            boundary=NAME_GATE_BOUNDARY,
            expected=f"resolved logical name from args.toolName (not {_MCP_WIRE!r})",
            arrived=f"resolved={resolved_name!r} wire={wire_name!r}",
            detail="resolve_stream_tool_name returned wire name — item-18 mcp/cortex mismatch class",
        )
        result = BoundaryEmitResult(value=(wire_name, args), shape_label=shape, violation=violation)
        if strict:
            from services.git_integration_worker.cursor_sdk_boundary_contract import (
                BoundaryContractError,
            )

            raise BoundaryContractError(violation)
    return result


def _register() -> None:
    register_boundary_contract(
        NAME_GATE_BOUNDARY,
        classify=classify_name_gate_shape,
        validate=_validate_name_gate_emit,
    )


_register()

__all__ = [
    "NAME_GATE_BOUNDARY",
    "classify_name_gate_shape",
    "emit_name_gate_boundary",
]
