"""Model-gate event emission for scheduler claim / release / lookup lifecycle.

Bus-only emits announcing per-step model-gate transitions: deferral when a
runnable step cannot yet acquire its model claim, claim/release pairs as
the scheduler arbitrates concurrent step access to a shared model, a
failure-boundary release variant for failed step execution, and a registry
lookup-failure event when model resolution itself raises. Imports from
``src.scheduling.events`` are kept lazy inside each function to break a
potential circular import between the universal-stargate pipeline package
and the scheduling subsystem (preserved verbatim from the prior monolith).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .context import get_event_context, publish_event

if TYPE_CHECKING:
    from .step_observability import StepObservability


def emit_pipeline_step_model_deferred(
    obs: StepObservability,
    *,
    step_id: str,
    model_id: str,
    reason: str,
) -> None:
    """Emit model-gate deferral for a runnable step."""
    from src.scheduling.events import PipelineStepModelDeferred

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineStepModelDeferred(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_id=step_id,
            model_id=model_id,
            reason=reason,
        ),
    )


def emit_pipeline_model_gate_claimed(
    obs: StepObservability, *, step_id: str, model_id: str
) -> None:
    """Emit event when a model gate claim is acquired for a step."""
    from src.scheduling.events import PipelineModelGateClaimed

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineModelGateClaimed(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_id=step_id,
            model_id=model_id,
        ),
    )


def emit_pipeline_model_gate_released(
    obs: StepObservability,
    *,
    step_id: str,
    model_id: str,
    outcome: str,
) -> None:
    """Emit event when a model gate claim is released for a step."""
    from src.scheduling.events import PipelineModelGateReleased

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineModelGateReleased(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_id=step_id,
            model_id=model_id,
            outcome=outcome,
        ),
    )


def emit_pipeline_model_gate_released_on_failure(
    obs: StepObservability,
    *,
    step_id: str,
    model_id: str,
    error_type: str,
) -> None:
    """Emit failure-boundary gate release event for failed step execution."""
    from src.scheduling.events import PipelineModelGateReleasedOnFailure

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineModelGateReleasedOnFailure(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_id=step_id,
            model_id=model_id,
            error_type=error_type,
        ),
    )


def emit_pipeline_model_registry_lookup_failed(
    obs: StepObservability,
    *,
    step_id: str,
    model_ref: str,
    error: str,
) -> None:
    """Emit event when model registry resolution fails for a step."""
    from src.scheduling.events import PipelineModelRegistryLookupFailed

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineModelRegistryLookupFailed(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_id=step_id,
            model_ref=model_ref,
            error=error,
        ),
    )
