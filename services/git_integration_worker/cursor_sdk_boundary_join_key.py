"""Join-key boundary — entity slug and call_id must both index assertion ids (item 17)."""

from __future__ import annotations

from collections.abc import Mapping

from services.git_integration_worker.cursor_sdk_boundary_contract import (
    BoundaryEmitResult,
    BoundaryShapeViolation,
    register_boundary_contract,
    validate_at_emit,
    _summarize_value,
)
from services.git_integration_worker.cursor_sdk_stream_capture import ToolCallObservation

JOIN_KEY_BOUNDARY = "join_key"


def classify_join_key_shape(value: object) -> str:
    """Label an assertion index emit at the join-key boundary."""
    if not isinstance(value, Mapping):
        return "non_mapping"
    if not value:
        return "empty"
    keys = list(value.keys())
    has_entity = any(not str(k).startswith(("tool_", "stream-")) for k in keys)
    has_call = any(str(k).startswith(("tool_", "stream-")) for k in keys)
    if has_entity and has_call:
        return "dual_axis"
    if has_entity:
        return "entity_only"
    if has_call:
        return "call_id_only"
    return "unknown_keys"


def _validate_join_key_emit(value: object, shape_label: str) -> BoundaryShapeViolation | None:
    if shape_label in {"empty", "non_mapping", "unknown_keys"}:
        return None
    if shape_label == "dual_axis":
        return None
    if not isinstance(value, Mapping):
        return None
    return BoundaryShapeViolation(
        boundary=JOIN_KEY_BOUNDARY,
        expected="index keys on BOTH entity slug and boundary call_id for each harvestable write",
        arrived=f"shape={shape_label} keys={sorted(str(k) for k in value.keys())[:6]}",
        detail=(
            "reconcile join requires entity_to_aid and call_id_to_aid — "
            "attempt-7 entity-slug versus tool-call-id split class"
        ),
    )


def emit_join_key_boundary(
    index: dict[str, str],
    *,
    source_observations: tuple[ToolCallObservation, ...] = (),
    strict: bool = False,
) -> BoundaryEmitResult:
    """Validate assertion index keys at the join-key emit point."""
    result = validate_at_emit(JOIN_KEY_BOUNDARY, index, strict=strict)
    if result.violation is not None or not source_observations:
        return result
    from services.git_integration_worker.cursor_sdk_cortex_identity import (
        assertion_id_from_cortex_observation,
        entity_key_from_observation,
    )

    for obs in source_observations:

        aid = assertion_id_from_cortex_observation(obs)
        if aid is None:
            continue
        entity_key = entity_key_from_observation(obs)
        call_id = obs.call_id
        missing: list[str] = []
        if entity_key and entity_key not in index:
            missing.append(f"entity={entity_key!r}")
        if call_id and call_id not in index:
            missing.append(f"call_id={call_id!r}")
        if missing:
            violation = BoundaryShapeViolation(
                boundary=JOIN_KEY_BOUNDARY,
                expected="index contains both entity slug and call_id for harvestable obs",
                arrived=f"index={_summarize_value(index)} missing={','.join(missing)}",
                detail="join_key index incomplete for observation with harvestable assertion id",
            )
            result = BoundaryEmitResult(value=index, shape_label=result.shape_label, violation=violation)
            if strict:
                from services.git_integration_worker.cursor_sdk_boundary_contract import (
                    BoundaryContractError,
                )

                raise BoundaryContractError(violation)
            return result
    return result


def _register() -> None:
    register_boundary_contract(
        JOIN_KEY_BOUNDARY,
        classify=classify_join_key_shape,
        validate=_validate_join_key_emit,
    )


_register()

__all__ = [
    "JOIN_KEY_BOUNDARY",
    "classify_join_key_shape",
    "emit_join_key_boundary",
]
