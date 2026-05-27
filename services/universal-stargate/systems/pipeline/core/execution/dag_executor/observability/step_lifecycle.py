"""Per-step lifecycle event emission (condition / skipped / started / inputs).

Dual-path emission: each helper writes a recorder dataclass event
(``events.lifecycle.*``, guarded by a recorder presence check) and
publishes the corresponding bus event factory (``events.step.*``,
imported as ``Bus*`` aliases to disambiguate from the lifecycle types).

``emit_step_inputs`` is the one helper that does no bus publish — it
hands resolved input snapshots to the recorder only, since these payloads
can be large and are intended for replay/debug consumers, not the live
event bus.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....events.lifecycle import (
    StepConditionEvaluated,
    StepInputsCaptured,
    StepSkipped,
    StepStarted,
)
from ....events.step import StepConditionEvaluated as BusStepConditionEvaluated
from ....events.step import StepSkipped as BusStepSkipped
from ....events.step import StepStarted as BusStepStarted
from .context import get_event_context, publish_event
from .input_capture import capture_step_inputs

if TYPE_CHECKING:
    from ....dag import StepNode
    from .step_observability import StepObservability


def emit_condition_evaluated(
    obs: StepObservability,
    *,
    node: StepNode,
    condition_expr: str,
    should_execute: bool,
    available_outputs: list[str],
) -> None:
    """Emit condition evaluation event to recorder and legacy bus."""
    recorder = obs._executor.context.recorder
    if recorder:
        recorder.emit(
            StepConditionEvaluated(
                step_name=node.step.name,
                condition=condition_expr,
                result=should_execute,
                available_outputs=available_outputs,
            )
        )
    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        BusStepConditionEvaluated(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_name=node.step.name,
            condition=condition_expr,
            result=should_execute,
            available_outputs=available_outputs,
        ),
    )


def emit_step_skipped(obs: StepObservability, *, node: StepNode, reason: str) -> None:
    """Emit skip event to recorder and legacy bus."""
    recorder = obs._executor.context.recorder
    if recorder:
        recorder.emit(
            StepSkipped(
                step_name=node.step.name,
                reason=reason,
            )
        )
    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        BusStepSkipped(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_name=node.step.name,
            reason=reason,
        ),
    )


def emit_step_started(
    obs: StepObservability, *, node: StepNode, target_model: str | None
) -> None:
    """Emit step started events to recorder and legacy bus."""
    recorder = obs._executor.context.recorder
    if recorder:
        recorder.emit(
            StepStarted(
                step_name=node.step.name,
                step_type=node.step.type,
                model_id=target_model,
                is_map_step=node.step.is_map_step,
            )
        )
    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        BusStepStarted(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            step_name=node.step.name,
            step_type=node.step.type,
            model_id=target_model,
            is_map_step=node.step.is_map_step,
        ),
    )


def emit_step_inputs(obs: StepObservability, *, node: StepNode) -> None:
    """Capture and emit step inputs for recorder observability."""
    recorder = obs._executor.context.recorder
    if not recorder:
        return
    inputs = capture_step_inputs(obs, node.step)
    if inputs:
        recorder.emit(StepInputsCaptured(step_name=node.step.name, inputs=inputs))
