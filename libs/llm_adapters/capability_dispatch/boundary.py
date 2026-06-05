"""Single resolution boundary — the one place max-output + reasoning resolve.

Per G7 this boundary holds WITHIN the frontier stack: ``gen_params`` calls
``resolve_dispatch`` with the FULL ``provider/model`` admission id (before
``api_model_id`` strips the provider for the adapter), and the adapters become
pure consumers of the resolved values. CP and WB are out-of-claim independent
resolvers — they call the same registry but stay separate resolution sites.

This module stays libs-pure (no gateway-service import). It returns
event-ready data; the services boundary emits the pinned G2 events
(``capability_dispatch.resolved`` / ``.knob_rejected`` / ``.catalog_miss``)
from that data. ``ProtocolError`` (G9) and ``CatalogMissError`` (G13) carry the
field payloads for the ``.knob_rejected`` / ``.catalog_miss`` events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from model_id import ModelId

from .types import CapabilityDispatch
from .wrappers import ModelWrapper, wrapper_for

RESOLVED_EVENT = "capability_dispatch.resolved"
KNOB_REJECTED_EVENT = "capability_dispatch.knob_rejected"
CATALOG_MISS_EVENT = "capability_dispatch.catalog_miss"


@dataclass(frozen=True, slots=True)
class MaxOutputResolution:
    requested: int | None
    resolved: int
    decision: str  # "explicit" | "default" | "floor_bump" | "ceiling_clamp"
    floor: int | None
    ceiling: int | None
    reasoning_budget: int | None
    native_field: str


@dataclass(frozen=True, slots=True)
class ReasoningResolution:
    effort: str | None
    native: dict[str, Any] | None
    native_field_path: str | None


@dataclass(frozen=True, slots=True)
class DispatchResolution:
    api_surface: str
    max_output: MaxOutputResolution
    reasoning: ReasoningResolution

    def resolved_event_fields(self) -> dict[str, Any]:
        """Pinned ``capability_dispatch.resolved`` event payload (G2)."""
        return {
            "max_output_requested": self.max_output.requested,
            "max_output_resolved": self.max_output.resolved,
            "max_output_decision": self.max_output.decision,
            "max_output_floor": self.max_output.floor,
            "max_output_ceiling": self.max_output.ceiling,
            "reasoning_budget": self.max_output.reasoning_budget,
        }


def resolve_dispatch(
    model: str | ModelId,
    *,
    requested_max_output: int | None = None,
    thinking: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> DispatchResolution:
    """Resolve the full per-model dispatch at the single frontier boundary.

    ``model`` is the FULL admission id (``provider/model`` or a bare cloud id).
    Raises ``CatalogMissError`` (G13) for a provider-uninferable model and
    ``ProtocolError`` (G9, collect-all) for any unsupported declared knob.
    """
    wrapper: ModelWrapper = wrapper_for(model)
    dispatch: CapabilityDispatch = wrapper.dispatch
    api_model_id = ModelId.parse(model).api_model_id

    # G9 — reject every unsupported declared knob before resolving values.
    wrapper.reject_unsupported_knobs(api_model_id, reasoning_effort=reasoning_effort)

    budget = wrapper.reasoning_budget(thinking)
    resolved, decision = wrapper.resolve_max_output(requested_max_output, thinking)
    max_output = MaxOutputResolution(
        requested=requested_max_output,
        resolved=resolved,
        decision=decision,
        floor=dispatch.max_output.floor,
        ceiling=dispatch.max_output.ceiling,
        reasoning_budget=budget,
        native_field=dispatch.max_output.native_field,
    )

    native = (
        wrapper.translate_reasoning(reasoning_effort)
        if reasoning_effort is not None
        else None
    )
    reasoning = ReasoningResolution(
        effort=reasoning_effort,
        native=native,
        native_field_path=(
            dispatch.reasoning.native_field_path if dispatch.reasoning else None
        ),
    )
    return DispatchResolution(
        api_surface=dispatch.api_surface,
        max_output=max_output,
        reasoning=reasoning,
    )
