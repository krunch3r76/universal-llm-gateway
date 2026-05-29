"""Ready-step scheduling: filtering, model-gated launch, and DAG bookkeeping.

Owns the per-pass scheduling surface that drives ``execute_dag``: detecting
``READY`` nodes, evaluating step conditions (skipping with an empty
``StepOutput`` and propagating the skip as a satisfied dependency), deferring
steps whose model gate is unavailable or already claimed this iteration,
launching the survivors as named asyncio tasks, propagating completion to
dependents (only ``COMPLETED``/``SKIPPED`` prerequisites unlock a dependent —
``FAILED`` is intentionally excluded), and the terminal-state predicates used
for deadlock/timeout diagnostics and completion telemetry. Functions take the
executor first and call sibling behavior through its delegators.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....dag import StepState

if TYPE_CHECKING:
    from ....dag import StepNode
    from .dag_executor import DAGExecutor

logger = get_logger(__name__)


async def process_ready_steps(executor: DAGExecutor) -> bool:
    """Filter and launch ready steps.

    Returns True when any progress is made in this pass:
    - one or more steps launched, or
    - one or more ready steps transitioned to SKIPPED.
    """
    ready_steps = [
        node for node in executor.nodes.values() if node.state == StepState.READY
    ]
    if not ready_steps:
        return False

    steps_to_launch = await executor._filter_ready_steps(ready_steps)
    skip_progress = any(node.state == StepState.SKIPPED for node in ready_steps)
    if not steps_to_launch:
        return skip_progress

    launched = await executor._launch_steps(steps_to_launch)
    return launched or skip_progress


async def launch_steps(executor: DAGExecutor, steps_to_launch: list[StepNode]) -> bool:
    """Launch selected steps as asyncio tasks."""
    launched_any = False
    models_in_use_this_iteration: set[str] = set()

    for node in steps_to_launch:
        target_model = await executor._model_coordination.resolve_target_model(node)
        lock_model = executor._model_coordination.get_lock_model(node, target_model)

        if lock_model:
            if not executor._model_coordination.can_launch_with_lock(lock_model):
                logger.debug(f"Step '{node.step.id}' waiting for model {lock_model}")
                executor._observability.emit_pipeline_step_model_deferred(
                    step_id=node.step.id,
                    model_id=lock_model,
                    reason="gate_unavailable",
                )
                continue
            if lock_model in models_in_use_this_iteration:
                logger.debug(f"Step '{node.step.id}' deferred: model already claimed")
                executor._observability.emit_pipeline_step_model_deferred(
                    step_id=node.step.id,
                    model_id=lock_model,
                    reason="gate_already_claimed",
                )
                continue

        node.state = StepState.RUNNING
        executor._model_coordination.on_step_launched(
            step_id=node.step.id,
            target_model=target_model,
            lock_model=lock_model,
            models_in_use_this_iteration=models_in_use_this_iteration,
        )
        task = asyncio.create_task(
            executor._execute_step(node), name=f"step-{node.step.id}"
        )
        executor._pending_tasks[node.step.id] = task
        launched_any = True

    return launched_any


async def filter_ready_steps(
    executor: DAGExecutor, ready_steps: list[StepNode]
) -> list[StepNode]:
    """Filter ready steps by condition and model availability."""
    from ....handlers.protocol import StepOutput

    steps_to_launch: list[StepNode] = []
    for node in ready_steps:
        should_execute, condition_expr = executor._should_execute_step(node.step)
        if condition_expr is not None:
            executor._observability.emit_condition_evaluated(
                node=node,
                condition_expr=condition_expr,
                should_execute=should_execute,
                available_outputs=list(executor.context.outputs.keys()),
            )

        if not should_execute:
            logger.info(
                "Step '%s' skipped (condition not met: %s)",
                node.step.id,
                condition_expr,
            )
            reason = f"condition not met: {condition_expr}"
            executor._observability.emit_step_skipped(node=node, reason=reason)
            skip_output = StepOutput(raw="", json={"_skipped": True})
            executor.context.set_output(node.step.id, skip_output)
            node.state = StepState.SKIPPED
            executor._propagate_completion(node.step.id)
            continue

        target_model = await executor._model_coordination.resolve_target_model(node)
        lock_model = executor._model_coordination.get_lock_model(node, target_model)
        if lock_model and not executor._model_coordination.can_launch_with_lock(
            lock_model
        ):
            logger.debug(f"Step '{node.step.id}' deferred: model in use")
            continue

        steps_to_launch.append(node)

    return steps_to_launch


def propagate_completion(executor: DAGExecutor, completed_step_id: str) -> None:
    """Mark dependents ready when all prerequisites are terminal-success states.

    Dependency satisfaction intentionally excludes FAILED prerequisites; only
    COMPLETED/SKIPPED permit downstream execution.
    """
    node = executor.nodes[completed_step_id]
    for dependent_id in node.dependents:
        dependent = executor.nodes[dependent_id]
        all_deps_done = all(
            executor.nodes[dep_id].state in (StepState.COMPLETED, StepState.SKIPPED)
            for dep_id in dependent.dependencies
        )
        if all_deps_done and dependent.state == StepState.PENDING:
            dependent.state = StepState.READY
            logger.debug(f"Step '{dependent_id}' now ready")


def incomplete_step_ids(executor: DAGExecutor) -> list[str]:
    """Return non-terminal step IDs for timeout/deadlock diagnostics."""
    return [
        node.step.id
        for node in executor.nodes.values()
        if node.state not in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
    ]


def all_done(executor: DAGExecutor) -> bool:
    """Check if all steps are complete."""
    return all(
        node.state in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
        for node in executor.nodes.values()
    )


def step_state_counts(executor: DAGExecutor) -> dict[StepState, int]:
    """Count terminal and intermediate step states for completion telemetry."""
    counts: dict[StepState, int] = {state: 0 for state in StepState}
    for node in executor.nodes.values():
        counts[node.state] += 1
    return counts
