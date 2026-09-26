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
    """Record a step's condition result to the recorder and publish it on the event bus.

    Called by ``filter_ready_steps`` only for steps that declare a condition or
    are disabled; carries the expression, boolean result, and the output names
    available at evaluation time for debugging skipped branches.
    """
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
    """Record a condition-driven step skip to the recorder and publish ``StepSkipped``
    on the bus.

    Called by ``filter_ready_steps`` before the node is marked ``SKIPPED``;
    ``reason`` is the ``condition not met: <expr>`` text.
    """
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
    """Record a step start to the recorder and publish ``StepStarted`` on the event bus.

    Called by ``execute_step`` right after execution-time model resolution;
    includes step type, the resolved ``target_model`` (may be None), and
    whether the step is a map step.
    """
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
    """Snapshot a step's resolved handler inputs into a recorder ``StepInputsCaptured``
    event.

    Recorder-only (no bus publish, payloads can be large); returns early without
    resolving inputs when no recorder is attached, and skips emission when the
    step has no captured inputs. Called by ``execute_step`` after step start.
    """
    recorder = obs._executor.context.recorder
    if not recorder:
        return
    inputs = capture_step_inputs(obs, node.step)
    if inputs:
        recorder.emit(StepInputsCaptured(step_name=node.step.name, inputs=inputs))
