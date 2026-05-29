"""Per-step execution: condition gate, handler/wrapper chain, model fallback.

Owns the path a single launched step travels: ``should_execute_step`` (enabled
flag + condition evaluation), ``execute_step`` (resolve the execution-time
target model, emit started/inputs observability, time the run, record
success/failure), ``run_step`` (map-step branch, then the standard run with
eligibility-gated model fallback — suppressed-fallback emits
``StepModelFallbackSuppressed`` and re-raises), ``run_step_inner`` (handler
through the wrapper chain), and ``try_step_model_fallback`` (delegate to the
fallback helper using the coordinator-resolved primary model identity so it
does not independently re-resolve). Functions take the executor first; the map
branch and inner run are reached through executor delegators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....events.step import StepModelFallbackSuppressed
from ...fallback_eligibility import classify_fallback_error

if TYPE_CHECKING:
    from ....dag import StepNode
    from ....handlers.protocol import StepOutput
    from ....schemas import StepConfig
    from ....step_config import ResolvedTargetModel
    from .dag_executor import DAGExecutor

logger = get_logger(__name__)


def should_execute_step(
    executor: DAGExecutor, step: StepConfig
) -> tuple[bool, str | None]:
    """Check if step should execute based on enabled flag and condition."""
    if not step.get_domain_field("enabled", True):
        return False, "enabled: false"

    if not step.condition:
        return True, None

    from ....conditions import evaluate_condition

    return (
        evaluate_condition(
            condition=step.condition,
            outputs=executor.context.outputs,
            options=executor.context.options,
        ),
        step.condition,
    )


async def execute_step(executor: DAGExecutor, node: StepNode) -> None:
    """Execute step and update state."""
    import time

    _mro = executor.context.options.get("model_ref_overrides")
    model_ref_overrides: dict[str, str] | None = (
        _mro if isinstance(_mro, dict) else None
    )
    resolve_target_model = (
        executor._model_coordination.resolve_target_model_resolution_for_execution
    )
    target_resolution = await resolve_target_model(node, model_ref_overrides)
    target_model = target_resolution.model_id if target_resolution else None

    executor._observability.emit_step_started(node=node, target_model=target_model)
    executor._observability.emit_step_inputs(node=node)

    start_time = time.time()
    try:
        output = await executor._run_step(
            node,
            target_model=target_model,
            target_resolution=target_resolution,
        )
        duration = time.time() - start_time
        executor._observability.record_success(node, output, duration)
    except Exception as e:
        duration = time.time() - start_time
        executor._observability.record_failure(node, e, duration)
        raise


async def run_step(
    executor: DAGExecutor,
    node: StepNode,
    *,
    target_model: str | None = None,
    target_resolution: ResolvedTargetModel | None = None,
) -> StepOutput:
    """Execute step, falling back to alternative models on eligible failures.

    Receives the coordinator-resolved target model so fallback can use it
    as the authoritative primary model identity without re-resolving.
    """
    step = node.step
    logger.debug(f"Executing step '{step.name}' (type: {step.type})")

    if step.is_map_step:
        return await executor._execute_map_step(node)

    try:
        return await executor._run_step_inner(step)
    except Exception as primary_err:
        if not step.model_ref or not step.model_requirements:
            raise
        eligibility = classify_fallback_error(primary_err)
        if not eligibility.should_fallback:
            executor._observability.publish_event(
                StepModelFallbackSuppressed(
                    pipeline_id=executor.context.pipeline.id,
                    execution_id=executor.context.execution_id,
                    step_name=step.name,
                    primary_error_type=eligibility.error_type,
                    suppression_reason=eligibility.reason,
                )
            )
            raise
        return await executor._try_step_model_fallback(
            step,
            primary_err,
            target_model=target_model,
            target_resolution=target_resolution,
        )


async def run_step_inner(executor: DAGExecutor, step: StepConfig) -> StepOutput:
    """Execute step through the standard wrapper chain."""
    from ....handlers import HandlerRegistry
    from ...step_wrapper import execute_step_with_wrappers

    async def handler_fn() -> StepOutput:
        return await HandlerRegistry.execute(step, executor.context)

    return await execute_step_with_wrappers(
        step=step,
        handler_fn=handler_fn,
        checkpoint_manager=executor._checkpoint_manager,
    )


async def try_step_model_fallback(
    executor: DAGExecutor,
    step: StepConfig,
    primary_err: Exception,
    *,
    target_model: str | None = None,
    target_resolution: ResolvedTargetModel | None = None,
) -> StepOutput:
    """Delegate fallback with the coordinator-resolved model.

    Passes primary_model_id so fallback does not independently re-resolve,
    avoiding silent divergence from the coordinated model identity.
    """
    from ..step_model_fallback import try_step_model_fallback as run_fallback

    return await run_fallback(
        step,
        primary_err,
        primary_model_id=target_model,
        primary_resolution=target_resolution,
        run_step_fn=executor._run_step_inner,
        context=executor.context,
        get_event_context=executor._observability.get_event_context,
        publish_event=executor._observability.publish_event,
    )
