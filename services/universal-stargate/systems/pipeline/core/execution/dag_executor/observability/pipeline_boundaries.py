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
    """Publish ``PipelineExecutionTimedOut`` just before ``execute_dag`` raises on
    timeout.

    Carries the configured ``timeout_seconds`` option and the ids of steps still
    non-terminal at the deadline. Bus-only, fire-and-forget.
    """
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
    """Publish ``PipelineDeadlockDetected`` just before ``execute_dag`` raises on
    deadlock.

    Fired when no step was launched or skipped and no tasks are pending while
    steps remain incomplete; ``pending_task_count`` is currently always 0 from
    that caller. Bus-only, fire-and-forget.
    """
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
    """Publish ``PipelineExecutionCancelled`` after external cancel has stopped pending
    tasks.

    Emitted by the executor's ``cancel`` lifecycle function once each unfinished
    task was cancelled and its model gate released, listing the step ids that
    were actually interrupted. Bus-only, fire-and-forget.
    """
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
