"""Unwrap boundary contract — MCP wire vs SDK-wrapper tool-result shapes (item 17)."""

from __future__ import annotations

from collections.abc import Mapping

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryEmitResult,
    BoundaryShapeViolation,
    register_boundary_contract,
    validate_at_emit,
    violation_for_unwrap,
)
from services.git_integration_worker.cursor_sdk_tool_result import (
    assertion_id_from_payload,
    unwrap_tool_result,
)

UNWRAP_BOUNDARY = "unwrap"

_SHAPE_MCP = "mcp_content_text"
_SHAPE_SDK = "sdk_value_content_text_text"
_SHAPE_STRUCTURED = "structured_content"
_SHAPE_STATUS_VALUE = "status_value_string"
_SHAPE_PLAIN = "plain_mapping"
_SHAPE_NULL = "null"
_SHAPE_ERROR = "error_status"
_SHAPE_NON_MAPPING = "non_mapping"


def _has_mcp_content_text(result: Mapping[str, object]) -> bool:
    content = result.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return True
    return False


def _has_sdk_value_content_text_text(result: Mapping[str, object]) -> bool:
    value = result.get("value")
    if not isinstance(value, Mapping):
        return False
    content = value.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, Mapping):
            continue
        text_obj = block.get("text")
        if isinstance(text_obj, Mapping) and isinstance(text_obj.get("text"), str):
            return True
    return False


def classify_tool_result_shape(value: object) -> str:
    """Label the wire shape of a raw tool result at the unwrap boundary."""
    if value is None:
        return _SHAPE_NULL
    if not isinstance(value, Mapping):
        return _SHAPE_NON_MAPPING
    if value.get("status") == "error":
        return _SHAPE_ERROR
    if isinstance(value.get("structuredContent"), Mapping):
        return _SHAPE_STRUCTURED
    if _has_sdk_value_content_text_text(value):
        return _SHAPE_SDK
    if _has_mcp_content_text(value):
        return _SHAPE_MCP
    value_field = value.get("value")
    if isinstance(value_field, str) and value_field.strip():
        return _SHAPE_STATUS_VALUE
    if value_field is not None:
        return _SHAPE_PLAIN
    return _SHAPE_PLAIN


def _is_harvestable_cortex_payload(unwrapped: object | None) -> bool:
    if unwrapped is None:
        return False
    return assertion_id_from_payload(unwrapped) is not None


def _validate_unwrap_emit(value: object, shape_label: str) -> BoundaryShapeViolation | None:
    unwrapped = unwrap_tool_result(value)
    harvestable = _is_harvestable_cortex_payload(unwrapped)
    # Only enforce harvest contract when the input looks like a cortex assert ack.
    if shape_label in {_SHAPE_MCP, _SHAPE_SDK, _SHAPE_STRUCTURED, _SHAPE_STATUS_VALUE}:
        return violation_for_unwrap(
            shape_label=shape_label,
            value=value,
            unwrapped=unwrapped,
            harvestable=harvestable,
        )
    return None


def emit_unwrap_boundary(
    value: object,
    *,
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate a tool result at the unwrap boundary emit point."""
    return validate_at_emit(UNWRAP_BOUNDARY, value, strict=strict)


def _register() -> None:
    register_boundary_contract(
        UNWRAP_BOUNDARY,
        classify=classify_tool_result_shape,
        validate=_validate_unwrap_emit,
    )


_register()

__all__ = [
    "UNWRAP_BOUNDARY",
    "classify_tool_result_shape",
    "emit_unwrap_boundary",
]
