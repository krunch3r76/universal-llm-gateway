"""Completion handling for launched step tasks with fail-fast cancellation.

Owns ``await_and_handle_completions``: waits for the first of the pending
asyncio tasks to finish, maps the task name back to its node, records the
model-gate release outcome via the coordinator, and on the first failure marks
the node ``FAILED``, cancels every remaining pending task, and raises
``PipelineExecutionError`` — the fail-fast invariant. An unknown completed task
id is treated as a hard structural error (remaining tasks cancelled, raise).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....dag import PipelineExecutionError, StepState

if TYPE_CHECKING:
    from .dag_executor import DAGExecutor

logger = get_logger(__name__)


async def await_and_handle_completions(executor: DAGExecutor) -> None:
    """Wait for tasks and handle completion/failure."""
    if not executor._pending_tasks:
        return

    done, _ = await asyncio.wait(
        executor._pending_tasks.values(),
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in done:
        step_id = task.get_name().removeprefix("step-")
        node = executor.nodes.get(step_id)
        if node is None:
            for remaining_task in executor._pending_tasks.values():
                _ = remaining_task.cancel()
            raise PipelineExecutionError(
                f"Completed task has unknown step id: {step_id}"
            )

        _ = executor._pending_tasks.pop(step_id, None)
        target_model = await executor._model_coordination.resolve_target_model(node)

        try:
            task.result()
            executor._model_coordination.on_step_finished(
                step_id=step_id,
                target_model=target_model,
                outcome="success",
            )
        except Exception as e:
            logger.error(f"Step '{step_id}' failed: {e}", exc_info=True)
            node.state = StepState.FAILED
            node.error = e
            executor._model_coordination.on_step_finished(
                step_id=step_id,
                target_model=target_model,
                outcome="failure",
            )
            if target_model:
                executor._observability.emit_pipeline_model_gate_released_on_failure(
                    step_id=step_id,
                    model_id=target_model,
                    error_type=type(e).__name__,
                )

            for remaining_task in executor._pending_tasks.values():
                _ = remaining_task.cancel()
            raise PipelineExecutionError(f"Step '{step_id}' failed: {e}") from e
