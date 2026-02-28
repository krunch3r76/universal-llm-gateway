"""
DAG executor for pipeline step execution.

Executes pipeline steps respecting dependencies with automatic parallelization.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_event_bus import Event
from universal_logging import get_logger

from src.core.gateway_tracker import gateway_tracker

from ..dag import PipelineExecutionError, StepState

# New observability events (for JSONL recorder)
from ..events.lifecycle import (
    StepCompleted,
    StepConditionEvaluated,
    StepFailed,
    StepInputsCaptured,
    StepOutputCaptured,
    StepSkipped,
    StepStarted,
)

# Old bus event factories (for backward-compatible monitoring consumers)
from ..events.step import (
    StepCompleted as BusStepCompleted,
)
from ..events.step import (
    StepConditionEvaluated as BusStepConditionEvaluated,
)
from ..events.step import (
    StepFailed as BusStepFailed,
)
from ..events.step import (
    StepSkipped as BusStepSkipped,
)
from ..events.step import (
    StepStarted as BusStepStarted,
)
from .errors import StepTimeoutError
from .proxy_client import ProxyClient

if TYPE_CHECKING:
    from ..dag import StepNode
    from ..handlers.protocol import PipelineContext, StepOutput
    from ..schemas import StepConfig
    from .checkpoint import CheckpointManager

from .model_tracker import ModelUsageTracker

logger = get_logger(__name__)
# Dedicated logger for pipeline execution tracking (separate file, no truncation)
execution_logger = get_logger("systems.pipeline.execution")


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
        self.execution_order: list[str] = []  # Track completion order for summaries
        self._checkpoint_manager = checkpoint_manager

        # Track in-flight models to prevent concurrent usage
        self._model_tracker = ModelUsageTracker()
        # Pending tasks tracked across execute() iterations
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        # Warn once if event bus unavailable
        self._event_bus_warned: bool = False

        # ProxyClient for handler invocations
        self._proxy_client: ProxyClient | None = None

    def _get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from context."""
        pipeline = getattr(self.context, "pipeline", None)
        if pipeline is None:
            logger.error("Missing context.pipeline - using 'unknown' for events")
            pipeline_id = "unknown"
        else:
            pipeline_id = pipeline.id

        execution_id = getattr(self.context, "execution_id", None)
        if execution_id is None:
            logger.error("Missing context.execution_id - using 'unknown' for events")
            execution_id = "unknown"

        return pipeline_id, execution_id

    def _publish_event(self, event: Event) -> None:
        """
        Publish event via context's event bus (fire-and-forget).

        Pattern matches MapExecutor._publish_event() for consistency.
        Logs WARN once if event_bus unavailable (graceful degradation).
        """
        proxy = getattr(self.context, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            asyncio.create_task(event_bus.publish_async_nowait(event))
        elif not getattr(self, "_event_bus_warned", False):
            logger.warning("Event bus unavailable - events will not be published")
            self._event_bus_warned = True

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
        - Cancels all pending step tasks
        - Releases model locks from global tracker
        - Closes ProxyClient

        Safe to call multiple times.
        """
        # Cancel all pending tasks
        for step_id, task in list(self._pending_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Release model from tracker and global tracker
            node = self.nodes.get(step_id)
            if node:
                target_model = node.step.get_target_model_id(
                    self.context._registry,
                    domain=self.context.pipeline.domain,
                    search_path=self.context.pipeline.source_search_path,
                )
                if target_model:
                    self._model_tracker.release(target_model, step_id)
                    self._unregister_global_tracking(target_model, step_id)

        self._pending_tasks.clear()

        # Close HTTP client
        await self.shutdown()

    async def execute(self) -> None:
        """
        Execute all steps respecting dependencies.

        BATCH ROUTING: Before launching parallel steps, route entire batch
        atomically to prevent resource contention. Falls back to sequential
        routing if batch routing defers all steps.

        Uses asyncio for parallelization. Steps become READY when all
        their dependencies complete.

        Raises:
            PipelineExecutionError: On deadlock, timeout, or execution failure
        """
        import time

        # Initialize ProxyClient for handler invocations
        self.context.proxy_client = await self._ensure_proxy_client()

        # Get timeout from pipeline options (now a dict)
        timeout_seconds = self.context.options.get("timeout_seconds")
        deadline = time.time() + timeout_seconds if timeout_seconds else None

        while not self._all_done():
            # Check timeout
            if deadline and time.time() >= deadline:
                incomplete = [
                    n.step.id
                    for n in self.nodes.values()
                    if n.state
                    not in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
                ]
                raise PipelineExecutionError(
                    f"Pipeline execution exceeded timeout of {timeout_seconds}s. "
                    f"Incomplete steps: {incomplete}"
                )

            launched = await self._process_ready_steps()

            # CRITICAL: Must process pending tasks when non-empty to prevent deadlock
            # (no new steps can launch while waiting for a model, but completions
            # free resources)
            if self._pending_tasks:
                await self._await_and_handle_completions()
            elif not launched:
                await asyncio.sleep(0.1)  # Avoid tight loop when blocked

    async def _process_ready_steps(self) -> bool:
        """
        Filter and launch ready steps.

        Returns:
            True if any steps were launched, False otherwise
        """
        # Find ready steps
        ready_steps = [
            node for node in self.nodes.values() if node.state == StepState.READY
        ]

        if not ready_steps:
            return False

        # Filter steps by condition and model availability
        steps_to_launch = await self._filter_ready_steps(ready_steps)
        if not steps_to_launch:
            return False

        # Launch steps (routing handled via HTTP by ProxyClient)
        return self._launch_steps(steps_to_launch)

    def _launch_steps(self, steps_to_launch: list[StepNode]) -> bool:
        """Launch selected steps as asyncio tasks."""
        launched_any = False
        models_in_use_this_iteration: set[str] = set()

        for node in steps_to_launch:
            target_model = node.step.get_target_model_id(
                self.context._registry,
                domain=self.context.pipeline.domain,
                search_path=self.context.pipeline.source_search_path,
            )
            # Sub-pipeline steps share model_refs across parallel chains; exclusive
            # locking would serialize them. The inference server is FIFO and manages
            # its own queue — locking here only adds wall-clock latency.
            lock_model = (
                None
                if node.step.get_domain_field("_sub_pipeline_step")
                else target_model
            )

            # Check model availability
            if lock_model:
                if not self._model_tracker.can_acquire(lock_model):
                    logger.debug(
                        f"Step '{node.step.id}' waiting for model {lock_model}"
                    )
                    continue

                if lock_model in models_in_use_this_iteration:
                    logger.debug(
                        f"Step '{node.step.id}' deferred: model already claimed"
                    )
                    continue

            # Launch step
            node.state = StepState.RUNNING
            if lock_model:
                self._model_tracker.acquire(lock_model, node.step.id)
                models_in_use_this_iteration.add(lock_model)
            if target_model:
                # Eviction protection: prevent federation from routing models away
                # while a pipeline step is actively using them (lock or not).
                self._register_global_tracking(target_model, node.step.id)

            task = asyncio.create_task(
                self._execute_step(node), name=f"step-{node.step.id}"
            )
            self._pending_tasks[node.step.id] = task
            launched_any = True

        return launched_any

    async def _filter_ready_steps(self, ready_steps: list[StepNode]) -> list[StepNode]:
        """Filter ready steps by condition and model availability."""
        from ..handlers.protocol import StepOutput

        steps_to_launch = []
        for node in ready_steps:
            should_execute, condition_expr = await self._should_execute_step(node.step)
            pipeline_id, execution_id = self._get_event_context()
            if condition_expr is not None:
                available_outputs = list(self.context.outputs.keys())
                recorder = self.context.recorder
                if recorder:
                    recorder.emit(
                        StepConditionEvaluated(
                            step_name=node.step.name,
                            condition=condition_expr,
                            result=should_execute,
                            available_outputs=available_outputs,
                        )
                    )
                self._publish_event(
                    BusStepConditionEvaluated(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=node.step.name,
                        condition=condition_expr,
                        result=should_execute,
                        available_outputs=available_outputs,
                    )
                )

            if not should_execute:
                logger.info(
                    "Step '%s' skipped (condition not met: %s)",
                    node.step.id,
                    condition_expr,
                )
                reason = f"condition not met: {condition_expr}"

                # Emit step skipped (recorder + bus)
                recorder = self.context.recorder
                if recorder:
                    recorder.emit(
                        StepSkipped(
                            step_name=node.step.name,
                            reason=reason,
                        )
                    )
                self._publish_event(
                    BusStepSkipped(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=node.step.name,
                        reason=reason,
                    )
                )

                skip_output = StepOutput(raw="", json={"_skipped": True})
                self.context.set_output(node.step.id, skip_output)
                node.state = StepState.SKIPPED
                self._propagate_completion(node.step.id)
                continue

            target_model = node.step.get_target_model_id(
                self.context._registry,
                domain=self.context.pipeline.domain,
                search_path=self.context.pipeline.source_search_path,
            )
            lock_model = (
                None
                if node.step.get_domain_field("_sub_pipeline_step")
                else target_model
            )
            if lock_model and not self._model_tracker.can_acquire(lock_model):
                logger.debug(f"Step '{node.step.id}' deferred: model in use")
                continue

            steps_to_launch.append(node)

        return steps_to_launch

    async def _await_and_handle_completions(self) -> None:
        """Wait for tasks and handle completion/failure."""
        if not self._pending_tasks:
            # Check for deadlock
            ready_steps = [
                node for node in self.nodes.values() if node.state == StepState.READY
            ]
            if not ready_steps:
                # Deadlock - shouldn't happen with valid DAG
                incomplete = [
                    n.step.id
                    for n in self.nodes.values()
                    if n.state
                    not in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
                ]
                raise PipelineExecutionError(f"Deadlock: steps stuck: {incomplete}")
            return

        # Wait for at least one task to complete
        done, _ = await asyncio.wait(
            self._pending_tasks.values(),
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Process completed tasks
        for task in done:
            step_id = task.get_name().replace("step-", "")
            del self._pending_tasks[step_id]

            node = self.nodes[step_id]
            target_model = node.step.get_target_model_id(
                self.context._registry,
                domain=self.context.pipeline.domain,
                search_path=self.context.pipeline.source_search_path,
            )
            self._model_tracker.release(target_model, step_id)
            # Unregister from global tracker
            if target_model:
                self._unregister_global_tracking(target_model, step_id)

            try:
                task.result()
            except Exception as e:
                logger.error(f"Step '{step_id}' failed: {e}")
                node.state = StepState.FAILED
                node.error = e

                for remaining_task in self._pending_tasks.values():
                    _ = remaining_task.cancel()
                raise PipelineExecutionError(f"Step '{step_id}' failed: {e}") from e

    async def _should_execute_step(self, step: StepConfig) -> tuple[bool, str | None]:
        """Check if step should execute based on enabled flag and condition."""
        if not step.get_domain_field("enabled", True):
            return False, "enabled: false"

        if not step.condition:
            return True, None

        from ..conditions import evaluate_condition

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

        pipeline_id, execution_id = self._get_event_context()
        target_model = node.step.get_target_model_id(
            self.context._registry,
            domain=self.context.pipeline.domain,
            search_path=self.context.pipeline.source_search_path,
        )

        # Emit step started (recorder + bus)
        recorder = self.context.recorder
        if recorder:
            recorder.emit(
                StepStarted(
                    step_name=node.step.name,
                    step_type=node.step.type,
                    model_id=target_model,
                    is_map_step=node.step.is_map_step,
                )
            )
        self._publish_event(
            BusStepStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                step_type=node.step.type,
                model_id=target_model,
                is_map_step=node.step.is_map_step,
            )
        )

        # Capture step inputs for observability
        if recorder:
            inputs = self._capture_step_inputs(node.step)
            if inputs:
                recorder.emit(
                    StepInputsCaptured(step_name=node.step.name, inputs=inputs)
                )

        start_time = time.time()
        try:
            output = await self._run_step(node)
            duration = time.time() - start_time
            self._record_success(node, output, duration)
        except Exception as e:
            duration = time.time() - start_time
            self._record_failure(node, e, duration)
            raise

    async def _run_step(self, node: StepNode) -> StepOutput:
        """Execute step with appropriate strategy (regular or map)."""
        from ..handlers import HandlerRegistry
        from .step_wrapper import execute_step_with_wrappers

        step = node.step
        logger.debug(f"Executing step '{step.name}' (type: {step.type})")

        if step.is_map_step:
            return await self._execute_map_step(node)

        async def handler_fn():
            return await HandlerRegistry.execute(step, self.context)

        return await execute_step_with_wrappers(
            step=step,
            handler_fn=handler_fn,
            checkpoint_manager=self._checkpoint_manager,
        )

    def _record_success(
        self, node: StepNode, output: StepOutput, duration: float
    ) -> None:
        """Record successful step completion with auto-aggregated tokens."""
        step_calls = self.context.drain_step_calls(node.step.name)
        if step_calls:
            output.model_call_count = len(step_calls)
            if output.prompt_tokens == 0 and output.completion_tokens == 0:
                output.prompt_tokens = sum(c.prompt_tokens for c in step_calls)
                output.completion_tokens = sum(c.completion_tokens for c in step_calls)
                logger.debug(
                    "Step '%s': auto-aggregated %d tokens from %d model calls",
                    node.step.name,
                    output.prompt_tokens + output.completion_tokens,
                    len(step_calls),
                )

            self._log_step_model_calls(
                node.step.name, step_calls, duration, success=True
            )

        node.output = output
        node.state = StepState.COMPLETED
        progress_by_step = getattr(self.context, "_step_progress_by_step", {})
        if node.step.name in progress_by_step:
            progress_by_step.pop(node.step.name, None)
            setattr(self.context, "_step_progress_by_step", progress_by_step)
        # For map steps, the collection is already stored in context.outputs
        # by _execute_map_step. Don't overwrite it with the wrapper StepOutput
        if not node.step.is_map_step:
            self.context.set_output(node.step.name, output)
        self.execution_order.append(node.step.name)
        latency_ms = output.latency_ms
        logger.info(f"Step '{node.step.name}' completed (latency: {latency_ms:.0f}ms)")
        self._propagate_completion(node.step.name)

        # Emit step output + completed (recorder + bus)
        recorder = self.context.recorder
        if recorder:
            recorder.emit(
                StepOutputCaptured(
                    step_name=node.step.name,
                    model_id=output.model_id,
                    raw=output.raw,
                    json_data=output.json,
                    json_parse_error=getattr(output, "json_parse_error", None),
                    prompt_tokens=output.prompt_tokens,
                    completion_tokens=output.completion_tokens,
                    latency_ms=output.latency_ms,
                    model_call_count=getattr(output, "model_call_count", 0),
                    system_prompt=output.system_prompt,
                    user_prompt=output.user_prompt,
                    request_body=output.request_body,
                )
            )
            recorder.emit(
                StepCompleted(
                    step_name=node.step.name,
                    model_id=output.model_id,
                    duration_ms=duration * 1000,
                    prompt_tokens=output.prompt_tokens,
                    completion_tokens=output.completion_tokens,
                    model_call_count=getattr(output, "model_call_count", 0),
                )
            )
        pipeline_id, execution_id = self._get_event_context()
        output_length = len(output.text) if hasattr(output, "text") else 0
        exit_code: int | None = None
        if output.json and isinstance(output.json.get("exit_code"), int):
            exit_code = output.json["exit_code"]
        self._publish_event(
            BusStepCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                duration_seconds=duration,
                output_length=output_length,
                prompt_tokens=output.prompt_tokens,
                completion_tokens=output.completion_tokens,
                model_call_count=getattr(output, "model_call_count", 0),
                exit_code=exit_code,
                json_output_keys=(list(output.json.keys()) if output.json else None),
            )
        )

    def _record_failure(
        self, node: StepNode, error: Exception, duration: float
    ) -> None:
        """Record step failure."""
        all_calls = self.context.drain_step_calls(node.step.name)
        successful_calls = [
            call for call in all_calls if getattr(call, "success", True) is True
        ]
        total_prompt = sum(
            getattr(call, "prompt_tokens", 0) for call in successful_calls
        )
        total_completion = sum(
            getattr(call, "completion_tokens", 0) for call in successful_calls
        )
        if all_calls:
            self._log_step_model_calls(
                node.step.name, all_calls, duration, success=False
            )

        node.state = StepState.FAILED
        node.error = error

        pipeline_id, execution_id = self._get_event_context()
        call_contexts = None
        if all_calls:
            call_contexts = [
                {
                    "request_id": getattr(c, "snapshot_request_id", None),
                    "request_body": getattr(c, "request_body", {}),
                    "response_content": getattr(c, "content", None),
                    "model": (
                        getattr(c, "request_body", {}).get("model")
                        if isinstance(getattr(c, "request_body", None), dict)
                        else None
                    ),
                    "prompt_tokens": getattr(c, "prompt_tokens", 0),
                    "completion_tokens": getattr(c, "completion_tokens", 0),
                    "success": getattr(c, "success", True),
                }
                for c in all_calls
            ]
        progress_by_step = getattr(self.context, "_step_progress_by_step", {})
        step_progress = progress_by_step.pop(node.step.name, None)
        setattr(self.context, "_step_progress_by_step", progress_by_step)
        if isinstance(error, StepTimeoutError):
            error.prompt_tokens = total_prompt
            error.completion_tokens = total_completion
            error.model_call_count = len(all_calls)
            if isinstance(step_progress, dict):
                error.items_total = step_progress.get("items_total")
                error.items_completed = step_progress.get("items_completed")
        try:
            from ...pipeline_failure_debug import write_failure_debug

            write_failure_debug(
                pipeline_id=pipeline_id or "",
                execution_id=execution_id or "",
                step_id=node.step.name,
                error=error,
                call_contexts=call_contexts,
            )
        except Exception as e:
            logger.warning("Could not write failure debug file: %s", e)

        # Emit step failed (recorder + bus)
        import traceback as tb_mod

        recorder = self.context.recorder
        if recorder:
            recorder.emit(
                StepFailed(
                    step_name=node.step.name,
                    error=str(error),
                    duration_ms=duration * 1000,
                    traceback="".join(tb_mod.format_exception(error)),
                    model_calls=call_contexts,
                    prompt_tokens=total_prompt,
                    completion_tokens=total_completion,
                    model_call_count=len(all_calls),
                )
            )
        self._publish_event(
            BusStepFailed(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=node.step.name,
                duration_seconds=duration,
                error=str(error),
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
                model_call_count=len(all_calls),
            )
        )

    def _capture_step_inputs(self, step: StepConfig) -> dict[str, Any]:
        """Capture resolved handler inputs for observability.

        Mirrors BaseHandler._resolve_input: resolve() → traverse_path()
        so the event records the actual value, not intermediate objects
        (e.g., MapOutputCollection, StepOutput).
        """
        from ..execution.resolver import NamespaceResolver, traverse_path

        inputs: dict[str, Any] = {}
        if not step.handler_inputs:
            return inputs
        resolver = NamespaceResolver(self.context)
        for input_name, binding in step.handler_inputs.items():
            try:
                root = resolver.resolve(binding)
                val = (
                    traverse_path(
                        root,
                        binding.field_path,
                        step_name=step.id,
                        field_name=input_name,
                        binding_repr=str(binding),
                        resolver=resolver,
                    )
                    if binding.field_path
                    else root
                )
                # Truncate large string values for JSONL size
                if isinstance(val, str) and len(val) > 2000:
                    val = val[:2000] + f"... ({len(val)} chars total)"
                inputs[input_name] = {
                    "source": str(binding),
                    "value": val,
                }
            except Exception:
                inputs[input_name] = {"source": str(binding), "value": None}
        return inputs

    def _log_step_model_calls(
        self,
        step_name: str,
        calls: list[Any],
        duration: float,
        *,
        success: bool,
    ) -> None:
        """Log per-step model call summary to execution_logger.

        Emits one event per step with aggregated token counts and
        snapshot_request_ids for correlation with request/response
        snapshot files on disk.
        """
        _, execution_id = self._get_event_context()
        total_prompt = sum(c.prompt_tokens for c in calls)
        total_completion = sum(c.completion_tokens for c in calls)
        total_tokens = total_prompt + total_completion

        # Collect unique models and snapshot IDs from the calls
        models: list[str] = []
        snapshot_ids: list[str] = []
        for c in calls:
            model = c.request_body.get("model", "unknown")
            if model not in models:
                models.append(model)
            snap_id = getattr(c, "snapshot_request_id", None)
            if snap_id:
                snapshot_ids.append(snap_id)

        status = "completed" if success else "failed"
        model_str = ", ".join(models)
        snap_str = ", ".join(snapshot_ids) if snapshot_ids else "none"

        execution_logger.info(
            f"Step '{step_name}' {status}: "
            f"execution_id={execution_id}, "
            f"model=[{model_str}], calls={len(calls)}, "
            f"prompt_tokens={total_prompt}, "
            f"completion_tokens={total_completion}, "
            f"total_tokens={total_tokens}, "
            f"duration={duration:.2f}s, "
            f"snapshot_ids=[{snap_str}]"
        )

    async def _execute_map_step(self, node: StepNode) -> StepOutput:
        """
        Execute map step with MapExecutor.

        MAP is an execution mode, not a handler type. The step.type field
        contains the actual handler type (e.g., "generate"). type="map"
        is rejected at parse time by StepConfig.reject_map_type validator.
        """
        import time

        from ..handlers import HandlerRegistry
        from ..handlers.protocol import StepOutput
        from .map_reduce import MapExecutor
        from .resolver import NamespaceResolver

        step = node.step

        # Use step.type directly - it's always a real handler type
        # (type="map" is rejected at parse time)
        handler = HandlerRegistry.create_handler(
            self.context.domain,
            step.type,
            variant=self.context.pipeline.source_variant,
        )
        resolver = NamespaceResolver(self.context)

        # Get ProxyClient for cancel callback
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

        # Store the collection directly in context so subsequent steps can access
        # individual outputs via index (e.g., answer_all.0.raw)
        # NOTE: This violates the StepOutput type but is necessary for map step access
        self.context.outputs[step.name] = collection  # type: ignore

        # Also return a StepOutput for compatibility with the executor interface
        return StepOutput(
            raw=f"Map step completed with {len(collection)} outputs",
            json={"outputs": [o.json for o in collection.all_outputs()]},
            latency_ms=latency_ms,
        )

    def _propagate_completion(self, completed_step_id: str) -> None:
        """Mark dependent steps as ready if all their deps are done."""
        node = self.nodes[completed_step_id]

        for dependent_id in node.dependents:
            dependent = self.nodes[dependent_id]

            # Check if all dependencies are satisfied
            all_deps_done = all(
                self.nodes[dep_id].state in (StepState.COMPLETED, StepState.SKIPPED)
                for dep_id in dependent.dependencies
            )

            if all_deps_done and dependent.state == StepState.PENDING:
                dependent.state = StepState.READY
                logger.debug(f"Step '{dependent_id}' now ready")

    def _all_done(self) -> bool:
        """Check if all steps are complete."""
        return all(
            node.state in (StepState.COMPLETED, StepState.SKIPPED, StepState.FAILED)
            for node in self.nodes.values()
        )

    def _register_global_tracking(self, model_id: str, step_id: str) -> None:
        """
        Register pipeline step model usage with global tracker.

        Prevents eviction of models actively used by pipeline steps.

        Args:
            model_id: Model being used by step
            step_id: Step identifier (for synthetic request ID)
        """
        parsed_model_id = ModelId.parse(model_id)
        routing_key = parsed_model_id.routing_key

        # Synthetic request ID: pipeline_{execution_id}_{step_id}
        pipeline_request_id = f"pipeline_{self.context.execution_id}_{step_id}"

        # Get gateway name from context (pipeline inherits from parent request)
        gateway_name = (
            getattr(self.context, "selected_gateway_instance", None) or "localhost"
        )

        gateway_tracker.track_request(
            gateway_id=gateway_name,
            request_id=pipeline_request_id,
            routing_key=routing_key,
        )

        logger.debug(
            f"🔒 Registered pipeline step '{step_id}' with global tracker "
            + f"(model={model_id}, routing_key={routing_key}, gateway={gateway_name})"
        )

    def _unregister_global_tracking(self, model_id: str, step_id: str) -> None:
        """
        Unregister pipeline step model usage from global tracker.

        Called when step completes (success or failure) to release eviction protection.

        Args:
            model_id: Model that was used by step
            step_id: Step identifier (for synthetic request ID)
        """
        # Reconstruct synthetic request ID (must match _register_global_tracking)
        pipeline_request_id = f"pipeline_{self.context.execution_id}_{step_id}"

        # Get gateway name (same logic as registration)
        gateway_name = (
            getattr(self.context, "selected_gateway_instance", None) or "localhost"
        )

        # Deterministic cleanup: release eviction protection immediately.
        gateway_tracker.complete_request(gateway_name, pipeline_request_id)

        logger.debug(
            f"🔓 Unregistered pipeline step '{step_id}' from global tracker "
            + f"(model={model_id}, gateway={gateway_name})"
        )
