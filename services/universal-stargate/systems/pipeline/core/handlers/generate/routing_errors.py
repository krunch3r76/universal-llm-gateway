"""
Annotate ProxyClientError when handler-level fallback is routing-suppressed.

When ``get_fallback_suppression_reason`` returns a non-empty reason in
``handler.execute`` (i.e. the primary model resolved to a local routing
layer but ``model_requirements.source='cloud'`` would cross layers),
``_annotate_routing_mismatch_error`` attaches a ``__notes__`` entry to
the exception about to be re-raised so operators see how the primary was
resolved (registry ``model_ref``, ``model_requirements`` auto-select, or
raw ``model_ref``) and why the cross-layer fallback was suppressed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...schemas import StepConfig
    from ...step_config import ResolvedTargetModel


def _annotate_routing_mismatch_error(
    *,
    primary_err: Exception,
    step: StepConfig,
    primary_model: str,
    primary_resolution: ResolvedTargetModel | None,
) -> None:
    """Attach a clearer note when local primary fallback is suppressed."""
    if not hasattr(primary_err, "add_note"):
        return

    origin = "resolved model"
    if primary_resolution is not None:
        if primary_resolution.came_from_registry_model_ref and step.model_ref:
            origin = f"registry model_ref '{step.model_ref}'"
        elif primary_resolution.came_from_model_requirements:
            origin = "model_requirements"
        elif primary_resolution.model_ref:
            origin = f"model_ref '{primary_resolution.model_ref}'"

    primary_err.add_note(
        "Fallback suppressed due to routing layer mismatch: "
        f"step '{step.name}' resolved local primary model '{primary_model}' from "
        f"{origin}, but model_requirements.source='cloud' would cross routing layers."
    )
