"""
DAG executor for pipeline step execution.

Executes pipeline steps respecting dependencies with automatic parallelization.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ...dag import PipelineExecutionError, StepState
from ...events.step import StepModelFallbackSuppressed
from ..fallback_eligibility import classify_fallback_error
from ..proxy_client import ProxyClient
from .model_coordination import StepModelCoordinator
from .observability import StepObservability

if TYPE_CHECKING:
    from ...dag import StepNode
    from ...handlers.protocol import PipelineContext, StepOutput
    from ...schemas import StepConfig
    from ..checkpoint import CheckpointManager

logger = get_logger(__name__)


class DAGExecutor:
    """
    Execute pipeline DAG with automatic parallelization.

    Steps execute as soon as their dependencies are satisfied.
    Independent steps run in parallel using asyncio.

    CONCURRENCY INVARIANT:
    - Only this executor writes to context.outputs
    - Handlers return StepOutput, never write directly
    - This is the single-writer for parallel safety

    Invariants:
    - ∀ step: dependencies complete before step starts
    - Parallel steps share no mutable state
    - First failure cancels remaining (fail-fast)

    SKIP SEMANTICS:
    - SKIPPED counts as "dependency satisfied" (dependents become ready)
    - Dependents must guard against missing inputs via conditions
    - Skip propagation is NOT automatic (use conditions for skip chains)
    """

    def __init__(
        self,
        nodes: dict[str, StepNode],
        context: PipelineContext,
        checkpoint_manager: CheckpointManager | None = None,
    ):
        self.nodes = nodes
        self.context = context
        self.execution_order: list[str] = []
        self._checkpoint_manager = checkpoint_manager
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        self._proxy_client: ProxyClient | None = None
        self._observability = StepObservability(self)
        self._model_coordination = StepModelCoordinator(self)

    async def _ensure_proxy_client(self) -> ProxyClient:
        """Lazily initialize ProxyClient for handler invocations."""
        if self._proxy_client is None:
            self._proxy_client = ProxyClient.from_environment()
        return self._proxy_client

    async def shutdown(self) -> None:
        """Cleanup resources."""
        if self._proxy_client:
            await self._proxy_client.close()
            self._proxy_client = None

    async def cancel(self) -> None:
        """
        Cancel pipeline execution and cleanup resources.

        Called when client disconnects or external cancellation is requested.
        """
        cancelled_steps: list[str] = []
        for step_id, task in list(self._pending_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                cancelled_steps.append(step_id)

            node = self.nodes.get(step_id)
            if node:
                target_model = await self._model_coordination.resolve_target_model(node)
                self._model_coordination.on_cancelled_step(
                    step_id=step_id,
                    target_model=target_model,
                )

        self._pending_tasks.clear()
        self._observability.emit_pipeline_execution_cancelled(
            cancelled_steps=cancelled_steps,
        )
        await self.shutdown()

    async def execute(self) -> None:
        """
        Execute all steps respecting dependencies.

        Raises:
            PipelineExecutionError: On deadlock, timeout, or execution failure
        """
        import time

        self.context.proxy_client = await self._ensure_proxy_client()
        timeout_seconds = self.context.options.get("timeout_seconds")
        deadline = time.time() + timeout_seconds if timeout_seconds else None

        while not self._all_done():
            if deadline and time.time() >= deadline:
                incomplete = self._incomplete_step_ids()
                timeout_value = (
                    float(timeout_seconds) if timeout_seconds is not None else 0.0
                )
                self._observability.emit_pipeline_execution_timed_out(
                    timeout_seconds=timeout_value,
                    incomplete_steps=incomplete,
                )
                raise PipelineExecutionError(
                    f"Pipeline execution exceeded timeout of {timeout_seconds}s. "
                    f"Incomplete steps: {incomplete}"
                )

            progress = await self._process_ready_steps()
            if self._pending_tasks:
                await self._await_and_handle_completions()
            elif not progress and not self._pending_tasks and not self._all_done():
                incomplete = self._incomplete_step_ids()
                self._observability.emit_pipeline_deadlock_detected(
                    incomplete_steps=incomplete,
                    pending_task_count=0,
                )
                raise PipelineExecutionError(
                    "Deadlock detected: no runnable steps and no pending tasks. "
                    f"Incomplete steps: {incomplete}"
                )

        state_counts = self._step_state_counts()
        self._observability.emit_pipeline_dag_execution_completed(
            completed_count=state_counts[StepState.COMPLETED],
            skipped_count=state_counts[StepState.SKIPPED],
            failed_count=state_counts[StepState.FAILED],
            total_steps=len(self.nodes),
        )

    async def _process_ready_steps(self) -> bool:
        """Filter and launch ready steps.

        Returns True when any progress is made in this pass:
        - one or more steps launched, or
        - one or more ready steps transitioned to SKIPPED.
        """
        ready_steps = [
            node for node in self.nodes.values() if node.state == StepState.READY
        ]
        if not ready_steps:
            return False

        steps_to_launch = await self._filter_ready_steps(ready_steps)
        skip_progress = any(node.state == StepState.SKIPPED for node in ready_steps)
        if not steps_to_launch:
            return skip_progress

        launched = await self._launch_steps(steps_to_launch)
        return launched or skip_progress

    async def _launch_steps(self, steps_to_launch: list[StepNode]) -> bool:
        """Launch selected steps as asyncio tasks."""
        launched_any = False
        models_in_use_this_iteration: set[str] = set()

        for node in steps_to_launch:
            target_model = await self._model_coordination.resolve_target_model(node)
            lock_model = self._model_coordination.get_lock_model(node, target_model)

            if lock_model:
                if not self._model_coordination.can_launch_with_lock(lock_model):
                    logger.debug(
                        f"Step '{node.step.id}' waiting for model {lock_model}"
                    )
                    self._observability.emit_pipeline_step_model_deferred(
                        step_id=node.step.id,
                        model_id=lock_model,
                        reason="gate_unavailable",
                    )
                    continue
                if lock_model in models_in_use_this_iteration:
                    logger.debug(
                        f"Step '{node.step.id}' deferred: model already claimed"
                    )
                    self._observability.emit_pipeline_step_model_deferred(
                        step_id=node.step.id,
                        model_id=lock_model,
                        reason="gate_already_claimed",
                    )
                    continue

            node.state = StepState.RUNNING
            self._model_coordination.on_step_launched(
                step_id=node.step.id,
                target_model=target_model,
                lock_model=lock_model,
                models_in_use_this_iteration=models_in_use_this_iteration,
            )
            task = asyncio.create_task(
                self._execute_step(node), name=f"step-{node.step.id}"
            )
            self._pending_tasks[node.step.id] = task
            launched_any = True

        return launched_any

    async def _filter_ready_steps(self, ready_steps: list[StepNode]) -> list[StepNode]:
        """Filter ready steps by condition and model availability."""
        from ...handlers.protocol import StepOutput

        steps_to_launch: list[StepNode] = []
        for node in ready_steps:
            should_execute, condition_expr = self._should_execute_step(node.step)
            if condition_expr is not None:
                self._observability.emit_condition_evaluated(
                    node=node,
                    condition_expr=condition_expr,
                    should_execute=should_execute,
                    available_outputs=list(self.context.outputs.keys()),
                )

            if not should_execute:
                logger.info(
                    "Step '%s' skipped (condition not met: %s)",
                    node.step.id,
                    condition_expr,
                )
                reason = f"condition not met: {condition_expr}"
                self._observability.emit_step_skipped(node=node, reason=reason)
                skip_output = StepOutput(raw="", json={"_skipped": True})
                self.context.set_output(node.step.id, skip_output)
                node.state = StepState.SKIPPED
                self._propagate_completion(node.step.id)
                continue

            target_model = await self._model_coordination.resolve_target_model(node)
            lock_model = self._model_coordination.get_lock_model(node, target_model)
            if lock_model and not self._model_coordination.can_launch_with_lock(
                lock_model
            ):
                logger.debug(f"Step '{node.step.id}' deferred: model in use")
                continue

            steps_to_launch.append(node)

        return steps_to_launch

    async def _await_and_handle_completions(self) -> None:
        """Wait for tasks and handle completion/failure."""
        if not self._pending_tasks:
            return

        done, _ = await asyncio.wait(
            self._pending_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            step_id = task.get_name().removeprefix("step-")
            node = self.nodes.get(step_id)
            if node is None:
                for remaining_task in self._pending_tasks.values():
                    _ = remaining_task.cancel()
                raise PipelineExecutionError(
                    f"Completed task has unknown step id: {step_id}"
                )

            _ = self._pending_tasks.pop(step_id, None)
            target_model = await self._model_coordination.resolve_target_model(node)

            try:
                task.result()
                self._model_coordination.on_step_finished(
                    step_id=step_id,
                    target_model=target_model,
                    outcome="success",
                )
            except Exception as e:
                logger.error(f"Step '{step_id}' failed: {e}", exc_info=True)
                node.state = StepState.FAILED
                node.error = e
                self._model_coordination.on_step_finished(
                    step_id=step_id,
                    target_model=target_model,
                    outcome="failure",
                )
                if target_model:
                    self._observability.emit_pipeline_model_gate_released_on_failure(
                        step_id=step_id,
                        model_id=target_model,
                        error_type=type(e).__name__,
                    )

                for remaining_task in self._pending_tasks.values():
                    _ = remaining_task.cancel()
                raise PipelineExecutionError(f"Step '{step_id}' failed: {e}") from e

    def _should_execute_step(self, step: StepConfig) -> tuple[bool, str | None]:
        """Check if step should execute based on enabled flag and condition."""
        if not step.get_domain_field("enabled", True):
            return False, "enabled: false"

        if not step.condition:
            return True, None

        from ...conditions import evaluate_condition

        return (
            evaluate_condition(
                condition=step.condition,
                outputs=self.context.outputs,
                options=self.context.options,
            ),
            step.condition,
        )

    async def _execute_step(self, node: StepNode) -> None:
        """Execute step and update state."""
        import time

        _mro = self.context.options.get("model_ref_overrides")
        model_ref_overrides: dict[str, str] | None = (
            _mro if isinstance(_mro, dict) else None
        )
        target_model = (
            await self._model_coordination.resolve_target_model_for_execution(
                node,
                model_ref_overrides,
            )
        )

        self._observability.emit_step_started(node=node, target_model=target_model)
        self._observability.emit_step_inputs(node=node)

        start_time = time.time()
        try:
            output = await self._run_step(node)
            duration = time.time() - start_time
            self._observability.record_success(node, output, duration)
        except Exception as e:
            duration = time.time() - start_time
            self._observability.record_failure(node, e, duration)
            raise

    async def _run_step(self, node: StepNode) -> StepOutput:
        """Execute step, falling back to alternative models on eligible failures.

        After the primary model exhausts its full retry chain (including
        handler-level ProxyClientError fallback), the executor resolves
        model_requirements to find ranked alternatives and re-runs the
        entire wrapper chain for each. Each fallback model gets its own
        fresh retry+timeout allocation.
        """
        step = node.step
        logger.debug(f"Executing step '{step.name}' (type: {step.type})")

        if step.is_map_step:
            return await self._execute_map_step(node)

        try:
            return await self._run_step_inner(step)
        except Exception as primary_err:
            if not step.model_ref or not step.model_requirements:
                raise
            eligibility = classify_fallback_error(primary_err)
            if not eligibility.should_fallback:
                self._observability.publish_event(
                    StepModelFallbackSuppressed(
                        pipeline_id=self.context.pipeline.id,
                        execution_id=self.context.execution_id,
                        step_name=step.name,
                        primary_error_type=eligibility.error_type,
                        suppression_reason=eligibility.reason,
                    )
                )
                raise
            return await self._try_step_model_fallback(
                step,
                primary_err,
            )

    async def _run_step_inner(self, step: StepConfig) -> StepOutput:
        """Execute step through the standard wrapper chain."""
        from ...handlers import HandlerRegistry
        from ..step_wrapper import execute_step_with_wrappers

        async def handler_fn() -> StepOutput:
            return await HandlerRegistry.execute(step, self.context)

        return await execute_step_with_wrappers(
            step=step,
            handler_fn=handler_fn,
            checkpoint_manager=self._checkpoint_manager,
        )

    async def _try_step_model_fallback(
        self,
        step: StepConfig,
        primary_err: Exception,
    ) -> StepOutput:
        """Delegate to extracted step_model_fallback module."""
        from .step_model_fallback import try_step_model_fallback

        return await try_step_model_fallback(
            step,
            primary_err,
            run_step_fn=self._run_step_inner,
            context=self.context,
            get_event_context=self._observability.get_event_context,
            publish_event=self._observability.publish_event,
        )

    async def _execute_map_step(self, node: StepNode) -> StepOutput:
        """
        Execute map step with MapExecutor.

        MAP is an execution mode, not a handler type. The step.type field
        contains the actual handler type (e.g., "generate"). type="map"
        is rejected at parse time by StepConfig.reject_map_type validator.
        """
        import time

        from ...handlers import HandlerRegistry
        from ...handlers.protocol import StepOutput
        from ..map_reduce import MapExecutor
        from ..resolver import NamespaceResolver

        step = node.step
        handler = HandlerRegistry.create_handler(
            self.context.domain,
            step.type,
            variant=self.context.pipeline.source_variant,
        )
        resolver = NamespaceResolver(self.context)
        proxy_client = await self._ensure_proxy_client()

        executor = MapExecutor(
            step=step,
            handler=handler,
            resolver=resolver,
            runtime=self.context,
            checkpoint_manager=self._checkpoint_manager,
            cancel_callback=proxy_client.cancel,
        )

        start_time = time.time()
        collection = await executor.execute()
        latency_ms = (time.time() - start_time) * 1000

        self.context.set_output(step.id, collection)
        return StepOutput(
            raw=f"Map step completed with {len(collection)} outputs",
            json={"outputs": [o.json for o in collection.all_outputs()]},
            latency_ms=latency_ms,
        )

    def _propagate_completion(self, completed_step_id: str) -> None:
        """Mark dependents ready when all prerequisites are terminal-success states.

        Dependency satisfaction intentionally excludes FAILED prerequisites; only
        COMPLETED/SKIPPED permit downstream execution.
        """
        node = self.nodes[completed_step_id]
        for dependent_id in node.dependents:
            dependent = self.nodes[dependent_id]
            all_deps_done = all(
                self.nodes[dep_id].state in (StepState.COMPLETED, StepState.SKIPPED)
                for dep_id in dependent.dependencies
            )
            if all_deps_done and dependent.state == StepState.PENDING:
                dependent.state = StepState.READY
                logger.debug(f"Step '{dependent_id}' now ready")

    def _incomplete_step_ids(self) -> list[str]:
        """Return non-terminal step IDs for timeout/deadlock diagnostics."""
        return [
            node.step.id
            for node in self.nodes.values()
            if node.state
            not in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
        ]

    def _all_done(self) -> bool:
        """Check if all steps are complete."""
        return all(
            node.state in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
            for node in self.nodes.values()
        )

    def _step_state_counts(self) -> dict[StepState, int]:
        """Count terminal and intermediate step states for completion telemetry."""
        counts: dict[StepState, int] = {state: 0 for state in StepState}
        for node in self.nodes.values():
            counts[node.state] += 1
        return counts
