"""Proxy-client lifecycle and the top-level DAG execution loop.

Holds the four methods that bracket a ``DAGExecutor`` run: lazy ``ProxyClient``
construction, resource ``shutdown``, external ``cancel`` (best-effort drift
check + model-gate release per pending step), and ``execute_dag`` — the
schedule/await loop that enforces the optional timeout deadline, raises on
deadlock when no step is runnable and nothing is pending, and emits the final
DAG-completion telemetry. Each function takes the executor as first argument
and reaches scheduling/completion behavior through the executor's delegators so
the single-writer concurrency invariant is preserved.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....dag import PipelineExecutionError, StepState
from ...proxy_client import ProxyClient

if TYPE_CHECKING:
    from .dag_executor import DAGExecutor

logger = get_logger(__name__)


async def ensure_proxy_client(executor: DAGExecutor) -> ProxyClient:
    """Lazily initialize ProxyClient for handler invocations."""
    if executor._proxy_client is None:
        executor._proxy_client = ProxyClient.from_environment()
    return executor._proxy_client


async def shutdown(executor: DAGExecutor) -> None:
    """Cleanup resources."""
    if executor._proxy_client:
        await executor._proxy_client.close()
        executor._proxy_client = None


async def cancel(executor: DAGExecutor) -> None:
    """Cancel pipeline execution and cleanup resources.

    Called when client disconnects or external cancellation is requested.
    Best-effort drift check during cleanup - logs but does not raise.
    """
    cancelled_steps: list[str] = []
    for step_id, task in list(executor._pending_tasks.items()):
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            cancelled_steps.append(step_id)

        node = executor.nodes.get(step_id)
        if node:
            target_model = await executor._model_coordination.resolve_target_model(node)
            try:
                executor._model_coordination.validate_resolution_consistency(
                    node, target_model
                )
            except Exception:
                logger.warning(
                    "Drift check raised during cancellation for step '%s' "
                    "(best-effort, not raised)",
                    step_id,
                    exc_info=True,
                )
            executor._model_coordination.on_cancelled_step(
                step_id=step_id,
                target_model=target_model,
            )

    executor._pending_tasks.clear()
    executor._observability.emit_pipeline_execution_cancelled(
        cancelled_steps=cancelled_steps,
    )
    await executor.shutdown()


async def execute_dag(executor: DAGExecutor) -> None:
    """
    Execute all steps respecting dependencies.

    Raises:
        PipelineExecutionError: On deadlock, timeout, or execution failure
    """
    import time

    executor.context.proxy_client = await executor._ensure_proxy_client()
    timeout_seconds = executor.context.options.get("timeout_seconds")
    deadline = time.time() + timeout_seconds if timeout_seconds else None

    while not executor._all_done():
        if deadline and time.time() >= deadline:
            incomplete = executor._incomplete_step_ids()
            timeout_value = (
                float(timeout_seconds) if timeout_seconds is not None else 0.0
            )
            executor._observability.emit_pipeline_execution_timed_out(
                timeout_seconds=timeout_value,
                incomplete_steps=incomplete,
            )
            raise PipelineExecutionError(
                f"Pipeline execution exceeded timeout of {timeout_seconds}s. "
                f"Incomplete steps: {incomplete}"
            )

        progress = await executor._process_ready_steps()
        if executor._pending_tasks:
            await executor._await_and_handle_completions()
        elif not progress and not executor._pending_tasks and not executor._all_done():
            incomplete = executor._incomplete_step_ids()
            executor._observability.emit_pipeline_deadlock_detected(
                incomplete_steps=incomplete,
                pending_task_count=0,
            )
            raise PipelineExecutionError(
                "Deadlock detected: no runnable steps and no pending tasks. "
                f"Incomplete steps: {incomplete}"
            )

    state_counts = executor._step_state_counts()
    executor._observability.emit_pipeline_dag_execution_completed(
        completed_count=state_counts[StepState.COMPLETED],
        skipped_count=state_counts[StepState.SKIPPED],
        failed_count=state_counts[StepState.FAILED],
        total_steps=len(executor.nodes),
    )
