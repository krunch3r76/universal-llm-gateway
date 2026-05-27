"""DAG execution loop with lifecycle event emission.

Owns the timeout/cancelled/failed branches that emit
``PipelineCompleted``/``PipelineFailed``/``PipelineCancelled`` to both the
JSONL recorder and the event bus. The post-success outcome assembly lives
in ``outcome_assembly`` so this module stays under the 300-SLOC ceiling.

Invariants:
- ∀ step: dependencies complete before execution (enforced by DAGExecutor).
- Parallel steps do not share mutable state.
- First failure propagates immediately (fail-fast).
- Only DAGExecutor writes to context.outputs.
- ``execute_async`` callers pass ``monitor_disconnect=False`` because the
  inbound connection closes immediately after the 202 response; running the
  disconnect monitor would cancel every async run at the first poll tick.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ..dag import StepState
from ..events.lifecycle import (
    PipelineCancelled,
    PipelineCompleted,
    PipelineFailed,
)
from ..events.pipeline import (
    PipelineCancelled as BusPipelineCancelled,
)
from ..events.pipeline import (
    PipelineCompleted as BusPipelineCompleted,
)
from ..events.pipeline import (
    PipelineFailed as BusPipelineFailed,
)
from ..execution.concurrency import maybe_concurrency_gate
from ..execution.disconnect_monitor import execute_with_disconnect_monitoring
from ..execution.outcome import PipelineExecutionOutcome
from .outcome_assembly import assemble_outcome
from .prepared import PreparedPipelineExecution, execution_logger, logger

if TYPE_CHECKING:
    from .pipeline_executor import PipelineExecutor


async def run_prepared_execution(
    executor: PipelineExecutor,
    prepared: PreparedPipelineExecution,
    *,
    monitor_disconnect: bool = True,
) -> PipelineExecutionOutcome:
    """Acquire per-chat concurrency gate (if declared) and run the DAG.

    Thin wrapper that serialises pipeline executions on a resolved string
    key when ``pipeline.concurrency.key`` is declared in the pipeline YAML,
    via the ``ConcurrencyBackend`` instance on ``executor._concurrency_backend``.
    No-op when the pipeline carries no ``concurrency:`` block — see
    ``execution.concurrency`` for the resolution rules and
    ``ConcurrencyLockTimeoutError`` for the timeout-failure shape.
    """
    async with maybe_concurrency_gate(
        prepared.pipeline,
        prepared.pipeline_context,
        executor._concurrency_backend,
    ):
        return await run_prepared_execution_inner(
            executor, prepared, monitor_disconnect=monitor_disconnect
        )


async def run_prepared_execution_inner(
    executor: PipelineExecutor,
    prepared: PreparedPipelineExecution,
    *,
    monitor_disconnect: bool = True,
) -> PipelineExecutionOutcome:
    """Execute the prepared DAG and return structured outcome.

    Emits ``PipelineCompleted``/``PipelineFailed``/``PipelineCancelled``
    on both the recorder and the event bus. On failure, re-raises the
    original exception with ``execution_id`` attached (preserving sync
    ``execute()`` contract). The recorder is flushed in the caller's
    ``finally`` block — this function does not close it.

    ``monitor_disconnect`` controls whether the execution races against
    a client-disconnection poller on ``pipeline_context.http_request``.
    Sync ``/v1/chat/completions`` callers hold a live connection for the
    duration of execution and want the cancel-on-disconnect ergonomics.
    Async ``/api/v1/pipelines/dispatch`` callers close the connection
    right after the 202 response — execution lifecycle is detached from
    the caller, so the monitor must be disabled (otherwise every
    non-trivial async run is cancelled at the first poll tick).
    """
    pipeline = prepared.pipeline
    pipeline_context = prepared.pipeline_context
    nodes = prepared.nodes
    dag_executor = prepared.dag_executor
    recorder = prepared.recorder

    start_time = prepared.start_monotonic or time.time()
    pipeline_timeout = float(pipeline_context.options.get("timeout_seconds", 60))

    try:
        if monitor_disconnect:
            execution_coro = execute_with_disconnect_monitoring(
                dag_executor=dag_executor,
                http_request=pipeline_context.http_request,
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                step_count=len(nodes),
            )
        else:
            execution_coro = dag_executor.execute()
        await asyncio.wait_for(execution_coro, timeout=pipeline_timeout)
        duration = time.time() - start_time

        recorder.emit(
            PipelineCompleted(
                duration_ms=duration * 1000,
                output_step=pipeline.output,
            ),
        )
        executor._publish_event(
            pipeline_context,
            BusPipelineCompleted(
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                duration_seconds=duration,
                step_count=len(nodes),
                output_step=pipeline.output,
            ),
        )
    except TimeoutError:
        duration = time.time() - start_time
        await dag_executor.cancel()

        error_msg = f"Pipeline '{pipeline.id}' timed out after {pipeline_timeout}s"
        execution_logger.error(
            "Pipeline execution timed out: pipeline=%s, "
            "execution_id=%s, timeout=%ss, duration=%.2fs",
            pipeline.id,
            pipeline_context.execution_id,
            pipeline_timeout,
            duration,
        )

        recorder.emit(
            PipelineFailed(
                duration_ms=duration * 1000,
                error=error_msg,
                failed_step=None,
                traceback="",
            ),
        )
        executor._publish_event(
            pipeline_context,
            BusPipelineFailed(
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                duration_seconds=duration,
                error=error_msg,
                failed_step=None,
            ),
        )
        exc = TimeoutError(error_msg)
        exc.execution_id = pipeline_context.execution_id  # type: ignore[attr-defined]
        raise exc from None
    except asyncio.CancelledError:
        duration = time.time() - start_time
        await dag_executor.cancel()

        completed_steps = sum(
            1
            for node in nodes.values()
            if node.state in (StepState.COMPLETED, StepState.SKIPPED)
        )
        pending_steps = len(nodes) - completed_steps

        recorder.emit(
            PipelineCancelled(
                duration_ms=duration * 1000,
                reason="client_disconnected",
                completed_steps=completed_steps,
                pending_steps=pending_steps,
            ),
        )
        executor._publish_event(
            pipeline_context,
            BusPipelineCancelled(
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                duration_seconds=duration,
                reason="client_disconnected",
                completed_steps=completed_steps,
                pending_steps=pending_steps,
            ),
        )

        logger.info(
            "Pipeline '%s' cancelled after %.1fs "
            "(client disconnected, %d/%d steps completed)",
            pipeline.id,
            duration,
            completed_steps,
            len(nodes),
        )
        raise
    except Exception as e:
        duration = time.time() - start_time

        failed_step = None
        for node in nodes.values():
            if node.state == StepState.FAILED:
                failed_step = node.step.name
                break

        execution_logger.error(
            "Pipeline execution failed: pipeline=%s, "
            "execution_id=%s, duration=%.2fs, "
            "failed_step=%s, error=%s",
            pipeline.id,
            pipeline_context.execution_id,
            duration,
            failed_step,
            str(e),
        )

        import traceback as tb_mod

        recorder.emit(
            PipelineFailed(
                duration_ms=duration * 1000,
                error=str(e),
                failed_step=failed_step,
                traceback="".join(tb_mod.format_exception(e)),
            ),
        )
        executor._publish_event(
            pipeline_context,
            BusPipelineFailed(
                pipeline_id=pipeline.id,
                execution_id=pipeline_context.execution_id,
                duration_seconds=duration,
                error=str(e),
                failed_step=failed_step,
            ),
        )
        e.execution_id = pipeline_context.execution_id  # type: ignore[union-attr]
        raise

    return assemble_outcome(prepared, duration)
