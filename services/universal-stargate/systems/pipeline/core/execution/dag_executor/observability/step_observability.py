"""``StepObservability`` class shell — public method surface for DAG executor.

Owns the executor back-reference and warn-once event-bus flag, and exposes
the full method surface (``emit_*`` / ``record_*`` / ``get_event_context`` /
``publish_event`` / ``capture_step_inputs`` / ``log_step_model_calls``) that
``executor.py`` and ``model_coordination.py`` call through. Each public
method is a thin delegator to a free function in a sibling submodule;
intra-package cross-module calls go function-to-function (not via this
class facade) to keep module-level dependencies explicit.

Consumers continue to ``from .observability import StepObservability`` —
the package ``__init__.py`` re-exports this class as the sole public
surface, preserving the prior monolith's import path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_event_bus import Event

from . import (
    context,
    input_capture,
    model_call_logging,
    model_gate,
    outcomes,
    pipeline_boundaries,
    step_lifecycle,
)

if TYPE_CHECKING:
    from ....dag import StepNode
    from ....handlers.protocol import StepOutput
    from ....schemas import StepConfig
    from ..executor import DAGExecutor


class StepObservability:
    """Owns DAG executor observability and event emission behavior."""

    def __init__(self, executor: DAGExecutor) -> None:
        self._executor = executor
        self._event_bus_warned = False

    def get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from context."""
        return context.get_event_context(self)

    def publish_event(self, event: Event) -> None:
        """
        Publish event via context's event bus (fire-and-forget).

        Logs WARN once if event_bus unavailable.
        """
        context.publish_event(self, event)

    def emit_condition_evaluated(
        self,
        *,
        node: StepNode,
        condition_expr: str,
        should_execute: bool,
        available_outputs: list[str],
    ) -> None:
        """Emit condition evaluation event to recorder and legacy bus."""
        step_lifecycle.emit_condition_evaluated(
            self,
            node=node,
            condition_expr=condition_expr,
            should_execute=should_execute,
            available_outputs=available_outputs,
        )

    def emit_step_skipped(self, *, node: StepNode, reason: str) -> None:
        """Emit skip event to recorder and legacy bus."""
        step_lifecycle.emit_step_skipped(self, node=node, reason=reason)

    def emit_step_started(self, *, node: StepNode, target_model: str | None) -> None:
        """Emit step started events to recorder and legacy bus."""
        step_lifecycle.emit_step_started(self, node=node, target_model=target_model)

    def emit_step_inputs(self, *, node: StepNode) -> None:
        """Capture and emit step inputs for recorder observability."""
        step_lifecycle.emit_step_inputs(self, node=node)

    def emit_pipeline_execution_timed_out(
        self,
        *,
        timeout_seconds: float,
        incomplete_steps: list[str],
    ) -> None:
        """Emit timeout boundary before pipeline timeout failure is raised."""
        pipeline_boundaries.emit_pipeline_execution_timed_out(
            self,
            timeout_seconds=timeout_seconds,
            incomplete_steps=incomplete_steps,
        )

    def emit_pipeline_deadlock_detected(
        self,
        *,
        incomplete_steps: list[str],
        pending_task_count: int,
    ) -> None:
        """Emit deadlock boundary before deadlock failure is raised."""
        pipeline_boundaries.emit_pipeline_deadlock_detected(
            self,
            incomplete_steps=incomplete_steps,
            pending_task_count=pending_task_count,
        )

    def emit_pipeline_execution_cancelled(self, *, cancelled_steps: list[str]) -> None:
        """Emit cancellation summary once task cancellation has completed."""
        pipeline_boundaries.emit_pipeline_execution_cancelled(
            self, cancelled_steps=cancelled_steps
        )

    def emit_pipeline_dag_execution_completed(
        self,
        *,
        completed_count: int,
        skipped_count: int,
        failed_count: int,
        total_steps: int,
    ) -> None:
        """Emit final DAG completion summary after all terminal states reached."""
        pipeline_boundaries.emit_pipeline_dag_execution_completed(
            self,
            completed_count=completed_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            total_steps=total_steps,
        )

    def emit_pipeline_step_model_deferred(
        self,
        *,
        step_id: str,
        model_id: str,
        reason: str,
    ) -> None:
        """Emit model-gate deferral for a runnable step."""
        model_gate.emit_pipeline_step_model_deferred(
            self, step_id=step_id, model_id=model_id, reason=reason
        )

    def emit_pipeline_model_gate_claimed(self, *, step_id: str, model_id: str) -> None:
        """Emit event when a model gate claim is acquired for a step."""
        model_gate.emit_pipeline_model_gate_claimed(
            self, step_id=step_id, model_id=model_id
        )

    def emit_pipeline_model_gate_released(
        self,
        *,
        step_id: str,
        model_id: str,
        outcome: str,
    ) -> None:
        """Emit event when a model gate claim is released for a step."""
        model_gate.emit_pipeline_model_gate_released(
            self, step_id=step_id, model_id=model_id, outcome=outcome
        )

    def emit_pipeline_model_gate_released_on_failure(
        self,
        *,
        step_id: str,
        model_id: str,
        error_type: str,
    ) -> None:
        """Emit failure-boundary gate release event for failed step execution."""
        model_gate.emit_pipeline_model_gate_released_on_failure(
            self, step_id=step_id, model_id=model_id, error_type=error_type
        )

    def emit_pipeline_model_registry_lookup_failed(
        self,
        *,
        step_id: str,
        model_ref: str,
        error: str,
    ) -> None:
        """Emit event when model registry resolution fails for a step."""
        model_gate.emit_pipeline_model_registry_lookup_failed(
            self, step_id=step_id, model_ref=model_ref, error=error
        )

    def record_success(
        self, node: StepNode, output: StepOutput, duration: float
    ) -> None:
        """Record successful step completion with auto-aggregated tokens."""
        outcomes.record_success(self, node, output, duration)

    def record_failure(self, node: StepNode, error: Exception, duration: float) -> None:
        """Record step failure, preserving timeout/debug metadata semantics."""
        outcomes.record_failure(self, node, error, duration)

    def capture_step_inputs(self, step: StepConfig) -> dict[str, Any]:
        """Capture resolved handler inputs for observability."""
        return input_capture.capture_step_inputs(self, step)

    def log_step_model_calls(
        self,
        step_name: str,
        calls: list[Any],
        duration: float,
        *,
        success: bool,
    ) -> None:
        """Log per-step model call summary to execution logger."""
        model_call_logging.log_step_model_calls(
            self, step_name, calls, duration, success=success
        )
