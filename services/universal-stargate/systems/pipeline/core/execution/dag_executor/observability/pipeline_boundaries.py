"""Pipeline-boundary event emission (timeout / deadlock / cancel / dag-completed).

Bus-only emits at pipeline lifecycle boundaries — these events do not have
a recorder lifecycle counterpart because they describe the executor's own
top-level state transitions, not per-step outcomes. Imports from
``src.scheduling.events`` are kept lazy inside each function to break a
potential circular import between the universal-stargate pipeline package
and the scheduling subsystem (preserved verbatim from the prior monolith).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .context import get_event_context, publish_event

if TYPE_CHECKING:
    from .step_observability import StepObservability


def emit_pipeline_execution_timed_out(
    obs: StepObservability,
    *,
    timeout_seconds: float,
    incomplete_steps: list[str],
) -> None:
    """Emit timeout boundary before pipeline timeout failure is raised."""
    from src.scheduling.events import PipelineExecutionTimedOut

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineExecutionTimedOut(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            timeout_seconds=timeout_seconds,
            incomplete_steps=incomplete_steps,
        ),
    )


def emit_pipeline_deadlock_detected(
    obs: StepObservability,
    *,
    incomplete_steps: list[str],
    pending_task_count: int,
) -> None:
    """Emit deadlock boundary before deadlock failure is raised."""
    from src.scheduling.events import PipelineDeadlockDetected

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineDeadlockDetected(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            incomplete_steps=incomplete_steps,
            pending_task_count=pending_task_count,
        ),
    )


def emit_pipeline_execution_cancelled(
    obs: StepObservability, *, cancelled_steps: list[str]
) -> None:
    """Emit cancellation summary once task cancellation has completed."""
    from src.scheduling.events import PipelineExecutionCancelled

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineExecutionCancelled(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            cancelled_steps=cancelled_steps,
        ),
    )


def emit_pipeline_dag_execution_completed(
    obs: StepObservability,
    *,
    completed_count: int,
    skipped_count: int,
    failed_count: int,
    total_steps: int,
) -> None:
    """Emit final DAG completion summary after all terminal states reached."""
    from src.scheduling.events import PipelineDagExecutionCompleted

    pipeline_id, execution_id = get_event_context(obs)
    publish_event(
        obs,
        PipelineDagExecutionCompleted(
            pipeline_id=pipeline_id,
            execution_id=execution_id,
            completed_count=completed_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            total_steps=total_steps,
        ),
    )
