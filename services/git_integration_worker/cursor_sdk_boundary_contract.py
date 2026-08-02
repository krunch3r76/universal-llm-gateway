"""Boundary contract mechanism — validate artifact shape at emit (item 17).

Values crossing layer boundaries must conform to a declared shape; silent
pass-through is the defect class this module rejects.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BoundaryShapeViolation:
    """Legible failure record for a non-conforming boundary crossing (AC-17b)."""

    boundary: str
    expected: str
    arrived: str
    detail: str

    def legible_message(self) -> str:
        return (
            f"boundary={self.boundary} expected={self.expected} "
            f"arrived={self.arrived} detail={self.detail}"
        )


@dataclass(frozen=True)
class BoundaryEmitResult:
    """Outcome of ``validate_at_emit`` — value plus optional violation flag."""

    value: object
    shape_label: str
    violation: BoundaryShapeViolation | None = None

    @property
    def conforming(self) -> bool:
        return self.violation is None


class BoundaryContractError(Exception):
    """Raised when ``strict=True`` and a boundary emit fails validation."""

    def __init__(self, violation: BoundaryShapeViolation) -> None:
        self.violation = violation
        super().__init__(violation.legible_message())


class BoundaryContract(Protocol):
    boundary: str

    def classify(self, value: object) -> str: ...

    def validate_emit(self, value: object) -> BoundaryEmitResult: ...


ShapeClassifier = Callable[[object], str]
ShapeValidator = Callable[[object, str], BoundaryShapeViolation | None]

_REGISTRY: dict[str, tuple[ShapeClassifier, ShapeValidator]] = {}


def register_boundary_contract(
    boundary: str,
    *,
    classify: ShapeClassifier,
    validate: ShapeValidator,
) -> None:
    """Register one boundary's classify + validate pair."""
    _REGISTRY[boundary] = (classify, validate)


def validate_at_emit(
    boundary: str,
    value: object,
    *,
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate ``value`` at the named boundary emit point (AC-17a)."""
    entry = _REGISTRY.get(boundary)
    if entry is None:
        raise KeyError(f"unregistered boundary contract: {boundary}")
    classify, validate_fn = entry
    shape_label = classify(value)
    violation = validate_fn(value, shape_label)
    result = BoundaryEmitResult(value=value, shape_label=shape_label, violation=violation)
    if strict and violation is not None:
        raise BoundaryContractError(violation)
    return result


def _summarize_value(value: object, *, max_len: int = 120) -> str:
    if value is None:
        return "null"
    if isinstance(value, Mapping):
        keys = sorted(str(k) for k in value.keys())
        preview = "{" + ",".join(keys[:8]) + ("..." if len(keys) > 8 else "") + "}"
        return preview[:max_len]
    text = repr(value)
    return text[:max_len] + ("..." if len(text) > max_len else "")


def violation_for_unwrap(
    *,
    shape_label: str,
    value: object,
    unwrapped: object | None,
    harvestable: bool,
) -> BoundaryShapeViolation | None:
    """Shared unwrap-boundary validator — used by ``cursor_sdk_boundary_unwrap``."""
    if shape_label == "null":
        return None
    if shape_label == "error_status":
        return None
    if harvestable:
        return None
    if unwrapped is None and shape_label == "sdk_value_content_text_text":
        return BoundaryShapeViolation(
            boundary="unwrap",
            expected=(
                "harvestable payload (mapping with item.id) after unwrap from "
                "declared shapes: mcp_content_text | sdk_value_content_text_text | "
                "structured_content | status_value_string"
            ),
            arrived=f"shape={shape_label} {_summarize_value(value)}",
            detail=(
                "unwrap returned None for SDK-wrapper shape "
                "{status,value:{content:[{text:{text}}]}} — attempt-12 defect class"
            ),
        )
    if shape_label == "sdk_value_content_text_text" and not harvestable:
        return BoundaryShapeViolation(
            boundary="unwrap",
            expected=(
                "harvestable payload (mapping with item.id) after unwrap from "
                "sdk_value_content_text_text"
            ),
            arrived=f"shape={shape_label} unwrapped={_summarize_value(unwrapped)}",
            detail=(
                "SDK-wrapper short-circuit returned nested content shell without "
                "parsed assert ack — attempt-12 defect class"
            ),
        )
    if unwrapped is None:
        return BoundaryShapeViolation(
            boundary="unwrap",
            expected="harvestable payload after unwrap from a declared input shape",
            arrived=f"shape={shape_label} {_summarize_value(value)}",
            detail="unwrap returned None",
        )
    return BoundaryShapeViolation(
        boundary="unwrap",
        expected="harvestable payload with item.id for cortex assert responses",
        arrived=f"shape={shape_label} unwrapped={_summarize_value(unwrapped)}",
        detail="unwrapped payload lacks harvestable assertion id",
    )


__all__ = [
    "BoundaryContract",
    "BoundaryContractError",
    "BoundaryEmitResult",
    "BoundaryShapeViolation",
    "register_boundary_contract",
    "validate_at_emit",
    "violation_for_unwrap",
]
