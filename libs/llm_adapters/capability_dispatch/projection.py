"""Pure preview projection for dispatch knob resolution (P1.2)."""

from __future__ import annotations

from typing import Any

from .boundary import resolve_dispatch
from .types import CatalogMissError, ProtocolError


def _rejected(
    *,
    resolved_model: str,
    requested_effort: str | None,
    reject_kind: str,
    violations: list[dict[str, Any]],
    provenance: str,
    note: str,
) -> dict[str, Any]:
    return {
        "requested_effort": requested_effort,
        "resolved_model": resolved_model,
        "rejected": True,
        "reject_kind": reject_kind,
        "violations": violations,
        "parity": "not_claimed",
        "provenance": provenance,
        "notes": [note],
    }


def project_knob_resolution(
    *,
    resolved_model: str,
    requested_effort: str | None,
    requested_max_output: int | None = None,
    provenance: str = "preview",
) -> dict[str, Any]:
    """Project knob resolution for dispatch envelopes; never raises."""
    try:
        resolution = resolve_dispatch(
            resolved_model,
            requested_max_output=requested_max_output,
            reasoning_effort=requested_effort,
        )
    except ProtocolError as exc:
        return _rejected(
            resolved_model=resolved_model,
            requested_effort=requested_effort,
            reject_kind="protocol_error",
            violations=[
                {
                    "knob": violation.knob,
                    "reject_code": violation.reject_code,
                    "message": violation.message,
                }
                for violation in exc.violations
            ],
            provenance=provenance,
            note=(
                "G9 reject-loudly: dispatch will fail with ProtocolError; "
                "this is not a resolution outcome"
            ),
        )
    except CatalogMissError as exc:
        return _rejected(
            resolved_model=resolved_model,
            requested_effort=requested_effort,
            reject_kind="catalog_miss",
            violations=[],
            provenance=provenance,
            note=exc.miss_reason,
        )
    except ValueError as exc:
        return _rejected(
            resolved_model=resolved_model,
            requested_effort=requested_effort,
            reject_kind="invalid_effort",
            violations=[],
            provenance=provenance,
            note=str(exc),
        )

    native = resolution.reasoning.native
    if requested_effort is None:
        status = "defaulted"
        notes = [
            (
                f"no effort requested; model default "
                f"'{resolution.reasoning.default}' applied at gen_params"
            )
        ]
    elif native is None:
        status = "no_thinking"
        notes = [
            f"effort '{requested_effort}' not in budget_map → no thinking emitted"
        ]
    else:
        status = "mapped"
        notes: list[str] = []

    if resolution.reasoning.value_kind == "adaptive" and requested_effort:
        notes.append(
            f"adaptive: output_config.effort={requested_effort} assembled "
            "downstream in gen_params; native shown is the translate_reasoning "
            "object only"
        )

    return {
        "requested_effort": requested_effort,
        "resolved_model": resolved_model,
        "api_surface": resolution.api_surface,
        "value_kind": resolution.reasoning.value_kind,
        "reasoning_native": native,
        "max_output": {
            "requested": resolution.max_output.requested,
            "resolved": resolution.max_output.resolved,
            "decision": resolution.max_output.decision,
            "floor": resolution.max_output.floor,
            "ceiling": resolution.max_output.ceiling,
        },
        "status": status,
        "parity": "not_claimed",
        "provenance": provenance,
        "notes": notes,
    }
