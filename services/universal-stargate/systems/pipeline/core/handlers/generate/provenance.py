"""
Source-provenance extraction and claim-provenance injection for generate steps.

- ``extract_source_provenance`` — when a generate step runs inside a map
  step (``context._map_state`` is set), pull the source iteration's
  provenance so the produced ``StepOutput`` records itself as a processor
  of that source rather than an originator. Returns ``None`` outside map
  context or when the source step has no provenance.

- ``inject_provenance_into_claims`` — when a JSON response contains known
  claim containers (``claims``, ``statements``, ``evaluations``), attach
  per-claim provenance built from the source originator plus this
  processor. Used by ``invoke.build_step_output`` when
  ``response_format=json_object`` and a ``source_provenance`` is present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ..protocol import PipelineContext


def extract_source_provenance(
    step: StepConfig,
    context: PipelineContext,
) -> dict[str, Any] | None:
    """
    Extract provenance from source data (for map steps processing answers).

    For decompose_all mapping over answer_all.*:
    - Source is answer_all.{key} output
    - Source provenance = answer author (originator)

    Returns None if no source provenance found.
    """
    # Check if this is a map step with iteration context
    if not getattr(context, "_map_state", None):
        return None

    map_state = context._map_state

    # Get the source step output (e.g., answer_all.phi)
    source_step_name = map_state.source_step_name  # e.g., "answer_all"
    iteration_key = map_state.iteration_key  # e.g., "phi"

    if not source_step_name:
        return None

    # Resolve source output
    source_output = context.get_output(source_step_name)
    if not source_output:
        return None

    # Handle MapOutputCollection
    from ...execution.map_reduce import MapOutputCollection

    if isinstance(source_output, MapOutputCollection):
        specific_output = (
            source_output.get_output_by_key(iteration_key)
            if iteration_key
            else source_output.get_output(map_state.iteration_index)
        )
        if specific_output and specific_output.provenance:
            return specific_output.provenance
    elif hasattr(source_output, "provenance") and source_output.provenance:
        return source_output.provenance

    return None


def inject_provenance_into_claims(
    json_data: dict[str, Any],
    source_provenance: dict[str, Any],
    processor_model_id: str,
    processor_step_id: str,
) -> dict[str, Any]:
    """
    Inject provenance into claim objects within JSON response.

    Handles common claim container patterns:
    - {"claims": [...]}
    - {"statements": [...]}
    - {"evaluations": [...]}

    Each claim gets:
    - originator from source_provenance
    - processor added to lineage
    """
    from provenance import Provenance

    # Build claim provenance (source originator + this processor)
    prov = Provenance.from_dict(source_provenance)
    prov = prov.with_processor(
        step_id=processor_step_id,
        processor_model_id=processor_model_id,
    )
    claim_provenance = prov.to_dict()

    # Inject into known claim containers
    for key in ("claims", "statements", "evaluations"):
        if key in json_data and isinstance(json_data[key], list):
            for item in json_data[key]:
                if isinstance(item, dict) and "provenance" not in item:
                    item["provenance"] = claim_provenance

    return json_data
