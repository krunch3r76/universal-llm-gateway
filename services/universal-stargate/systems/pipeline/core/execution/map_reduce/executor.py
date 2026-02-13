"""
Map executor with concurrent iteration and partial success support.

Executes map steps with full concurrency, supporting:
- Timeout-based partial completion
- Fail-fast on impossible thresholds
- Per-iteration checkpoint integration
"""

import asyncio
import logging
import math
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .collection import MapOutputCollection
from .iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from universal_event_bus import Event

    from ...schemas import InputBinding, StepConfig, StepOutput
    from ..checkpoint import CheckpointManager
    from ..resolver import NamespaceResolver

# Old bus event factories (for backward-compatible monitoring consumers)
# New observability events (for JSONL recorder)
from ...events.lifecycle import MapIterationCompleted
from ...events.map import (
    MapIterationCompleted as BusMapIterationCompleted,
)
from ...events.map import (
    MapIterationFailed,
    MapIterationStarted,
    MapStepStarted,
    MapTimeoutWarning,
)
from ...events.map import (
    MapStepCompleted as BusMapStepCompleted,
)

logger = logging.getLogger(__name__)


class MapExecutor:
    """
    Executes map steps with fan-out parallelism.

    Execution flow:
    1. Resolve map_over to list/dict
    2. For each item, create MapState and inject mapNs
    3. Execute handler with iteration context
    4. Return list of StepOutput (partial success if threshold configured)

    Async-safety:
    - All iterations execute concurrently
    - Each iteration gets fresh MapState (no shared state)
    - System-level concurrency managed by Stargate proxy

    Partial Success:
    - If timeout_seconds set: uses asyncio.wait() with timeout
    - If min_success_threshold set: allows partial completion
    - Returns successful results if threshold met
    - Raises MapPartialFailureError if below threshold
    """

    def __init__(
        self,
        step: "StepConfig",
        handler: Any,  # Handler instance with execute(step, context) signature
        resolver: "NamespaceResolver",
        runtime: Any,  # RuntimeContext protocol
        checkpoint_manager: "CheckpointManager | None" = None,
        cancel_callback: "Callable[[str, str | None], Awaitable[bool]] | None" = None,
    ):
        self._step = step
        self._handler = handler
        self._resolver = resolver
        self._runtime = runtime
        self._checkpoint_manager = checkpoint_manager
        self._cancel_callback = cancel_callback
        # Cache parsed MapConfig for efficiency (avoid repeated parsing)
        self._map_config = step.get_map_config()

    def _get_event_context(self) -> tuple[str, str]:
        """Extract pipeline_id and execution_id from runtime context."""
        pipeline_id = getattr(self._runtime, "pipeline", None)
        pipeline_id = pipeline_id.id if pipeline_id else "unknown"
        execution_id = getattr(self._runtime, "execution_id", "unknown")
        return pipeline_id, execution_id

    def _extract_source_step_name(self) -> str | None:
        """
        Extract source step name from map_over binding.

        For map_over: { answer: answer_all.* }
        Returns: "answer_all"

        For map_over: { model: optionsNs.models }
        Returns: None (not a step reference)
        """
        if not self._map_config or not self._map_config.map_over:
            return None

        # Get first binding (currently only single binding supported)
        field_name, binding = next(iter(self._map_config.map_over.items()))

        # Check if namespace is a step reference
        if hasattr(binding, "namespace") and binding.namespace == "step":
            return binding.step_name

        return None

    def _publish_event(self, event: "Event") -> None:
        """Publish event via runtime's event bus (fire-and-forget)."""
        proxy = getattr(self._runtime, "_proxy", None)
        event_bus = getattr(proxy, "event_bus", None) if proxy else None
        if event_bus:
            # Schedule async publish as task (fire-and-forget)
            asyncio.create_task(event_bus.publish_async_nowait(event))

    async def _timeout_warning_monitor(
        self,
        timeout_seconds: float,
        tasks: dict["asyncio.Task", int],
        start_time: float,
    ) -> None:
        """Emit warnings at 75% and 90% of timeout."""
        pipeline_id, execution_id = self._get_event_context()
        warned_75 = False
        warned_90 = False

        while True:
            await asyncio.sleep(5.0)  # Check every 5 seconds
            elapsed = time.monotonic() - start_time
            percent = elapsed / timeout_seconds

            pending_indices = [idx for task, idx in tasks.items() if not task.done()]
            completed = len(tasks) - len(pending_indices)

            if percent >= 0.75 and not warned_75:
                warned_75 = True
                self._publish_event(
                    MapTimeoutWarning(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        elapsed_seconds=elapsed,
                        timeout_seconds=timeout_seconds,
                        pending_iterations=pending_indices,
                        completed_iterations=completed,
                    )
                )
                logger.warning(
                    "[%s] Timeout warning: %.0f%% elapsed (%d/%d pending)",
                    self._step.name,
                    percent * 100,
                    len(pending_indices),
                    len(tasks),
                )

            if percent >= 0.90 and not warned_90:
                warned_90 = True
                self._publish_event(
                    MapTimeoutWarning(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        elapsed_seconds=elapsed,
                        timeout_seconds=timeout_seconds,
                        pending_iterations=pending_indices,
                        completed_iterations=completed,
                    )
                )
                logger.warning(
                    "[%s] Timeout warning: %.0f%% elapsed (%d/%d pending)",
                    self._step.name,
                    percent * 100,
                    len(pending_indices),
                    len(tasks),
                )

            if percent >= 1.0 or not pending_indices:
                break

    async def _cancel_pending_iterations(
        self,
        pending: set["asyncio.Task"],
        tasks: dict["asyncio.Task", int],
        iteration_context: dict[int, dict],
    ) -> None:
        """
        Cancel federation requests for timed-out iterations.

        Releases capacity and signals remote workers to stop.
        """
        if not pending:
            return

        cancel_fn = self._cancel_callback
        if cancel_fn is None:
            logger.warning(
                f"[{self._step.name}] No cancel callback available, "
                f"{len(pending)} requests may remain active"
            )
            return

        # Cancel each timed-out iteration
        cancel_tasks = []
        for task in pending:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            map_iteration_request_id = ctx.get("map_iteration_request_id")
            model_id = ctx.get("model_id")

            if map_iteration_request_id:
                logger.info(
                    f"[{self._step.name}] Cancelling iteration {idx}: "
                    f"{map_iteration_request_id[:8]}... (model={model_id})"
                )
                cancel_tasks.append(cancel_fn(map_iteration_request_id, model_id))
            else:
                logger.warning(
                    f"[{self._step.name}] No map_iteration_request_id "
                    f"for iteration {idx}, cannot cancel"
                )

        # Execute all cancellations in parallel
        if cancel_tasks:
            results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
            cancelled = 0
            failed = 0
            for i, result in enumerate(results):
                if result is True:
                    cancelled += 1
                elif isinstance(result, Exception):
                    failed += 1
                    logger.error(
                        f"[{self._step.name}] Cancel task {i} failed: {result}"
                    )
            logger.info(
                f"[{self._step.name}] Cancelled {cancelled}/{len(cancel_tasks)} "
                f"federation requests" + (f" ({failed} failed)" if failed else "")
            )

    async def execute(self) -> MapOutputCollection:
        """
        Execute map step.

        Returns MapOutputCollection for wildcard access.
        Uses partial success pattern if timeout/threshold configured.
        """
        if not self._map_config:
            raise ValueError(f"Step '{self._step.name}' missing map_config")

        start_time = time.monotonic()

        # Resolve map_over to iteration values
        iteration_items = self._resolve_map_over(self._map_config.map_over)
        total = len(iteration_items)

        # Handle empty collection gracefully
        if total == 0:
            logger.warning(
                "[%s] Map step has 0 iterations (empty map_over collection). "
                "Returning empty MapOutputCollection.",
                self._step.name,
            )
            # Return empty collection immediately
            return MapOutputCollection([], keys=[])

        # Emit start event
        pipeline_id, execution_id = self._get_event_context()
        self._publish_event(
            MapStepStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                total_iterations=total,
                timeout_seconds=self._map_config.timeout_seconds,
                threshold=self._map_config.min_success_threshold,
            )
        )

        logger.info(
            "[%s] Map step: %d iterations (timeout=%s, threshold=%s, fail_fast=%s)",
            self._step.name,
            total,
            self._map_config.timeout_seconds,
            self._map_config.min_success_threshold,
            self._map_config.fail_fast,
        )

        # Pre-compute model assignments from pool (if configured)
        pool_assignments: dict[int, str] = {}

        if self._map_config.model_pool:
            from ..resolver import traverse_path

            pool_binding = self._map_config.model_pool
            pool_root = self._resolver.resolve(pool_binding)
            pool = traverse_path(
                pool_root, pool_binding.field_path, resolver=self._resolver
            )

            if not isinstance(pool, list):
                raise TypeError(
                    f"model_pool must resolve to list, got {type(pool).__name__}"
                )
            if not pool:
                raise ValueError("model_pool resolved to empty list")

            for idx, value, key in iteration_items:
                model = self._select_from_pool(
                    pool=pool,
                    originator=key,
                    exclude_self=self._map_config.exclude_self,
                    selection=self._map_config.selection,
                    index=idx,
                )
                pool_assignments[idx] = model

            logger.info(
                "[%s] Model assignments (pool, exclude_self=%s, selection=%s): %s",
                self._step.name,
                self._map_config.exclude_self,
                self._map_config.selection,
                {key: pool_assignments[idx] for idx, _, key in iteration_items if key},
            )

        # Build iteration context for tracking
        iteration_context: dict[int, dict] = {}
        for idx, value, key in iteration_items:
            # Get assigned model from pool if available
            assigned_model = pool_assignments.get(idx)

            # Extract model/gateway info from step config for this iteration
            iter_inputs = self._prepare_iteration_inputs(
                idx, value, total, key, assigned_model
            )
            iter_step = self._create_iteration_step(iter_inputs[2], assigned_model)
            model_id_for_iteration = getattr(iter_step, "model_ref", None) or getattr(
                iter_step, "model_id", None
            )
            map_iteration_request_id = str(uuid.uuid4())
            iteration_context[idx] = {
                "model_id": model_id_for_iteration,
                "gateway_id": None,  # Populated during execution if available
                "started_at": time.monotonic(),
                "map_iteration_request_id": map_iteration_request_id,
            }

        # Create tasks for all iterations
        # Store iteration metadata alongside tasks
        iteration_metadata = [(idx, key) for idx, _, key in iteration_items]

        async def _tracked_iteration(
            idx: int, value: Any, key: str | None
        ) -> "StepOutput":
            """Wrapper to track individual iteration completion time."""
            result = await self._execute_iteration(
                idx, value, total, key, iteration_context, pool_assignments.get(idx)
            )
            # Record completion time for this specific iteration
            iteration_context[idx]["completed_at"] = time.monotonic()
            return result

        tasks = {
            asyncio.create_task(_tracked_iteration(idx, value, key)): idx
            for idx, value, key in iteration_items
        }

        # Execute with appropriate mode - pass iteration_context
        if self._map_config.fail_fast:
            outputs, output_keys = await self._execute_with_fail_fast(
                tasks,
                total,
                self._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
            )
        elif self._map_config.timeout_seconds is not None:
            outputs, output_keys = await self._execute_with_timeout(
                tasks,
                total,
                self._map_config.timeout_seconds,
                self._map_config.min_success_threshold,
                iteration_metadata,
                iteration_context,
            )
        else:
            # Strict mode: all must succeed, gather() preserves order
            outputs = await asyncio.gather(*tasks.keys())
            output_keys = [key for _, _, key in iteration_items]

        # Emit completion event
        duration = time.monotonic() - start_time
        succeeded_count = len(outputs)
        failed_count = total - succeeded_count
        met_threshold = self._success_count_meets_threshold(
            succeeded_count, total, self._map_config.min_success_threshold
        )
        self._publish_event(
            BusMapStepCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                total_count=total,
                duration_seconds=duration,
                met_threshold=met_threshold,
            )
        )

        return MapOutputCollection(list(outputs), keys=output_keys)

    async def _execute_with_timeout(
        self,
        tasks: dict["asyncio.Task", int],
        total: int,
        timeout_seconds: float,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict],  # {idx: {model_id, gateway_id, started_at}}
    ) -> tuple[list["StepOutput"], list[str | None]]:
        """
        Execute with timeout and optional partial success.

        Tracks per-iteration state for rich error reporting.

        On CancelledError (client disconnect), cancels all pending federation
        requests before propagating the exception.
        """
        from ..errors import MapPartialFailureError

        start_time = time.monotonic()

        # Start warning monitor
        monitor_task = asyncio.create_task(
            self._timeout_warning_monitor(timeout_seconds, tasks, start_time)
        )

        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                timeout=timeout_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
        except asyncio.CancelledError:
            # Client disconnected - cancel all federation requests
            logger.info(
                "[%s] Cancelled during execution, cancelling %d federation requests",
                self._step.name,
                len(tasks),
            )
            # Determine which tasks are still pending
            pending_tasks = {t for t in tasks if not t.done()}

            # Cancel local asyncio tasks
            for task in pending_tasks:
                task.cancel()

            # Cancel federation requests (releases queue slots, signals workers)
            await self._cancel_pending_iterations(
                pending_tasks, tasks, iteration_context
            )

            raise  # Re-raise CancelledError
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        # Cancel any pending tasks
        for task in pending:
            task.cancel()

        # Propagate cancellation to federation layer
        await self._cancel_pending_iterations(pending, tasks, iteration_context)

        # Build iteration results
        iteration_results: list[IterationResult] = []
        results_by_index: dict[int, StepOutput] = {}

        for task in done:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            started_at = ctx.get("started_at")
            # Use individual task completion time if available
            completion_time = ctx.get("completed_at", time.monotonic())
            duration = (completion_time - started_at) if started_at else None

            if task.exception() is not None:
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.FAILED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        error_message=str(task.exception()),
                        started_at=started_at,
                    )
                )
                logger.warning(
                    "[%s] Iteration %d failed: %s",
                    self._step.name,
                    idx,
                    task.exception(),
                )
            else:
                results_by_index[idx] = task.result()
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.COMPLETED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        started_at=started_at,
                    )
                )

        # Add timed-out tasks
        for task in pending:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            iteration_results.append(
                IterationResult(
                    index=idx,
                    status=IterationStatus.TIMEOUT,
                    model_id=ctx.get("model_id"),
                    gateway_id=ctx.get("gateway_id"),
                    duration_seconds=timeout_seconds,
                    started_at=ctx.get("started_at"),
                )
            )
            logger.warning("[%s] Iteration %d timed out", self._step.name, idx)

        # Sort by index for consistent ordering
        iteration_results.sort(key=lambda r: r.index)

        # Emit per-iteration events
        pipeline_id, execution_id = self._get_event_context()
        recorder = getattr(self._runtime, "recorder", None)
        key_by_idx = dict(iteration_metadata)
        for result in iteration_results:
            if result.status == IterationStatus.COMPLETED:
                self._publish_event(
                    BusMapIterationCompleted(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        iteration_index=result.index,
                        duration_seconds=result.duration_seconds or 0.0,
                    )
                )
                if recorder:
                    out = results_by_index.get(result.index)
                    recorder.emit(
                        MapIterationCompleted(
                            step_name=self._step.name,
                            model_id=result.model_id,
                            iteration_index=result.index,
                            iteration_key=key_by_idx.get(result.index) or "",
                            duration_ms=(result.duration_seconds or 0.0) * 1000,
                            output_text=(out.text if out else ""),
                            prompt_tokens=(out.prompt_tokens if out else 0),
                            completion_tokens=(out.completion_tokens if out else 0),
                        )
                    )
            else:
                if result.status == IterationStatus.TIMEOUT:
                    failure_type = "timeout"
                elif result.status == IterationStatus.CANCELLED:
                    failure_type = "cancelled"
                else:
                    failure_type = "error"
                error_msg = result.error_message or f"Iteration {result.status.value}"
                self._publish_event(
                    MapIterationFailed(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        iteration_index=result.index,
                        error=error_msg,
                        duration_seconds=result.duration_seconds,
                        failure_type=failure_type,
                    )
                )

        completed = len(results_by_index)
        failed = total - completed

        logger.info(
            "[%s] Map completed: %d/%d succeeded, %d failed/timed out",
            self._step.name,
            completed,
            total,
            failed,
        )

        # Check threshold
        if not self._success_count_meets_threshold(completed, total, threshold):
            # Detect gateway serialization
            gateway_counts = Counter(
                r.gateway_id for r in iteration_results if r.gateway_id
            )
            serialized_gateways = tuple(
                gw for gw, count in gateway_counts.items() if count > 1
            )

            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=completed,
                failed_count=failed,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=timeout_seconds,
                iteration_results=tuple(iteration_results),
                gateway_serialization=(
                    serialized_gateways if serialized_gateways else None
                ),
            )

        # Extract outputs and keys in iteration order (only successful ones)
        outputs = []
        output_keys = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)

        return outputs, output_keys

    def _success_count_meets_threshold(
        self,
        success_count: int,
        total: int,
        threshold: int | float | None,
    ) -> bool:
        """Check if success count meets the configured threshold."""
        if threshold is None:
            return success_count == total  # Strict mode: all must succeed

        if isinstance(threshold, int):
            return success_count >= threshold  # Count threshold

        # Percentage threshold
        return (success_count / total) >= threshold if total > 0 else True

    def _compute_required_success_count(
        self, total: int, threshold: int | float | None
    ) -> int:
        """Compute required success count from threshold configuration."""
        if threshold is None:
            return total  # Strict mode
        if isinstance(threshold, int):
            return threshold
        # Percentage - ceiling to be conservative
        return math.ceil(total * threshold)

    async def _execute_with_fail_fast(
        self,
        tasks: dict["asyncio.Task", int],
        total: int,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict],  # {idx: {model_id, gateway_id, started_at}}
    ) -> tuple[list["StepOutput"], list[str | None]]:
        """
        Execute with fail-fast on impossible threshold.

        Waits for all iterations to complete, but cancels remaining
        as soon as failures prove threshold is unreachable.
        Does NOT stop early on success - more results are always better.

        Returns:
            Tuple of (outputs, keys) where both lists are in iteration order
            and only contain successful iterations.
        """
        from ..errors import MapPartialFailureError

        done: set[asyncio.Task] = set()
        pending = set(tasks.keys())
        required = self._compute_required_success_count(total, threshold)

        while pending:
            newly_done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            done.update(newly_done)

            # Count current state
            success_count = sum(1 for t in done if t.exception() is None)
            failure_count = len(done) - success_count

            # Check if threshold is now impossible
            max_possible = success_count + len(pending)
            if max_possible < required:
                logger.warning(
                    "[%s] Fail-fast: %d failures make threshold impossible "
                    "(need %d, max possible %d). Cancelling %d pending.",
                    self._step.name,
                    failure_count,
                    required,
                    max_possible,
                    len(pending),
                )
                # Cancel remaining - can't possibly meet threshold
                for task in pending:
                    task.cancel()

                # Build iteration results for error
                iteration_results: list[IterationResult] = []
                ff_results_by_index: dict[int, StepOutput] = {}
                for task in done:
                    idx = tasks[task]
                    ctx = iteration_context.get(idx, {})
                    started_at = ctx.get("started_at")
                    # Use individual task completion time if available
                    completion_time = ctx.get("completed_at", time.monotonic())
                    duration = (completion_time - started_at) if started_at else None

                    if task.exception() is not None:
                        iteration_results.append(
                            IterationResult(
                                index=idx,
                                status=IterationStatus.FAILED,
                                model_id=ctx.get("model_id"),
                                gateway_id=ctx.get("gateway_id"),
                                duration_seconds=duration,
                                error_message=str(task.exception()),
                                started_at=started_at,
                            )
                        )
                    else:
                        ff_results_by_index[idx] = task.result()
                        iteration_results.append(
                            IterationResult(
                                index=idx,
                                status=IterationStatus.COMPLETED,
                                model_id=ctx.get("model_id"),
                                gateway_id=ctx.get("gateway_id"),
                                duration_seconds=duration,
                                started_at=started_at,
                            )
                        )

                # Add cancelled tasks
                for task in pending:
                    idx = tasks[task]
                    ctx = iteration_context.get(idx, {})
                    iteration_results.append(
                        IterationResult(
                            index=idx,
                            status=IterationStatus.CANCELLED,
                            model_id=ctx.get("model_id"),
                            gateway_id=ctx.get("gateway_id"),
                            started_at=ctx.get("started_at"),
                        )
                    )

                iteration_results.sort(key=lambda r: r.index)

                # Emit per-iteration events
                pipeline_id, execution_id = self._get_event_context()
                rec = getattr(self._runtime, "recorder", None)
                ff_key_by_idx = dict(iteration_metadata)
                for result in iteration_results:
                    if result.status == IterationStatus.COMPLETED:
                        self._publish_event(
                            BusMapIterationCompleted(
                                pipeline_id=pipeline_id,
                                execution_id=execution_id,
                                step_name=self._step.name,
                                iteration_index=result.index,
                                duration_seconds=result.duration_seconds or 0.0,
                            )
                        )
                        if rec:
                            out = ff_results_by_index.get(result.index)
                            rec.emit(
                                MapIterationCompleted(
                                    step_name=self._step.name,
                                    model_id=result.model_id,
                                    iteration_index=result.index,
                                    iteration_key=ff_key_by_idx.get(result.index) or "",
                                    duration_ms=(result.duration_seconds or 0.0) * 1000,
                                    output_text=(out.text if out else ""),
                                    prompt_tokens=(out.prompt_tokens if out else 0),
                                    completion_tokens=(
                                        out.completion_tokens if out else 0
                                    ),
                                )
                            )
                    else:
                        if result.status == IterationStatus.CANCELLED:
                            failure_type = "cancelled"
                        else:
                            failure_type = "error"
                        error_msg = (
                            result.error_message or f"Iteration {result.status.value}"
                        )
                        self._publish_event(
                            MapIterationFailed(
                                pipeline_id=pipeline_id,
                                execution_id=execution_id,
                                step_name=self._step.name,
                                iteration_index=result.index,
                                error=error_msg,
                                duration_seconds=result.duration_seconds,
                                failure_type=failure_type,
                            )
                        )

                # Detect gateway serialization
                gateway_counts = Counter(
                    r.gateway_id for r in iteration_results if r.gateway_id
                )
                serialized_gateways = tuple(
                    gw for gw, count in gateway_counts.items() if count > 1
                )

                raise MapPartialFailureError(
                    step_name=self._step.name,
                    completed_count=success_count,
                    failed_count=failure_count + len(pending),
                    total_count=total,
                    threshold=threshold if threshold is not None else total,
                    timeout_seconds=None,
                    iteration_results=tuple(iteration_results),
                    gateway_serialization=(
                        serialized_gateways if serialized_gateways else None
                    ),
                )

        # All completed - collect results by iteration index
        results_by_index: dict[int, StepOutput] = {}
        iteration_results: list[IterationResult] = []

        for task in done:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            started_at = ctx.get("started_at")
            # Use individual task completion time if available
            completion_time = ctx.get("completed_at", time.monotonic())
            duration = (completion_time - started_at) if started_at else None

            if task.exception() is not None:
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.FAILED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        error_message=str(task.exception()),
                        started_at=started_at,
                    )
                )
            else:
                results_by_index[idx] = task.result()
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.COMPLETED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        started_at=started_at,
                    )
                )

        iteration_results.sort(key=lambda r: r.index)
        success_count = len(results_by_index)

        # Emit per-iteration events
        pipeline_id, execution_id = self._get_event_context()
        rec2 = getattr(self._runtime, "recorder", None)
        ff2_key_by_idx = dict(iteration_metadata)
        for result in iteration_results:
            if result.status == IterationStatus.COMPLETED:
                self._publish_event(
                    BusMapIterationCompleted(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        iteration_index=result.index,
                        duration_seconds=result.duration_seconds or 0.0,
                    )
                )
                if rec2:
                    out = results_by_index.get(result.index)
                    rec2.emit(
                        MapIterationCompleted(
                            step_name=self._step.name,
                            model_id=result.model_id,
                            iteration_index=result.index,
                            iteration_key=ff2_key_by_idx.get(result.index) or "",
                            duration_ms=(result.duration_seconds or 0.0) * 1000,
                            output_text=(out.text if out else ""),
                            prompt_tokens=(out.prompt_tokens if out else 0),
                            completion_tokens=(out.completion_tokens if out else 0),
                        )
                    )
            else:
                if result.status == IterationStatus.CANCELLED:
                    failure_type = "cancelled"
                else:
                    failure_type = "error"
                error_msg = result.error_message or f"Iteration {result.status.value}"
                self._publish_event(
                    MapIterationFailed(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        iteration_index=result.index,
                        error=error_msg,
                        duration_seconds=result.duration_seconds,
                        failure_type=failure_type,
                    )
                )

        logger.info(
            "[%s] Fail-fast complete: %d/%d succeeded",
            self._step.name,
            success_count,
            total,
        )

        if not self._success_count_meets_threshold(success_count, total, threshold):
            # Detect gateway serialization
            gateway_counts = Counter(
                r.gateway_id for r in iteration_results if r.gateway_id
            )
            serialized_gateways = tuple(
                gw for gw, count in gateway_counts.items() if count > 1
            )

            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=success_count,
                failed_count=len(iteration_results) - success_count,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=None,
                iteration_results=tuple(iteration_results),
                gateway_serialization=(
                    serialized_gateways if serialized_gateways else None
                ),
            )

        # Extract outputs and keys in iteration order (only successful ones)
        outputs = []
        output_keys = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)

        return outputs, output_keys

    def _select_from_pool(
        self,
        pool: list[str],
        originator: str | None,
        exclude_self: bool,
        selection: str,
        index: int,
    ) -> str:
        """
        Select model from pool for iteration.

        Invariant: |pool| > 0 ⟹ returns valid model

        Args:
            pool: Available models
            originator: Current iteration key (model to potentially exclude)
            exclude_self: Whether to exclude originator from candidates
            selection: "random", "rotate", or "first"
            index: Iteration index (for rotate determinism)
        """
        candidates = pool

        if exclude_self and originator:
            candidates = [m for m in pool if m != originator]
            if not candidates:
                # Only option is self - use it (fallback)
                candidates = [originator]
                logger.debug(
                    "[%s] Pool exhausted after exclude_self, using originator: %s",
                    self._step.name,
                    originator,
                )

        if selection == "random":
            import random

            return random.choice(candidates)
        elif selection == "rotate":
            # Deterministic based on index for reproducibility
            return candidates[index % len(candidates)]
        else:  # first
            return candidates[0]

    def _resolve_map_over(
        self,
        bindings: dict[str, "InputBinding"],
    ) -> list[tuple[int, Any, str | None]]:
        """
        Resolve map_over bindings to iteration items.

        Returns list of (index, value, key) tuples.

        Supports:
        - list/dict from optionsNs
        - MapOutputCollection via step.* (wildcard means iterate collection)
        """
        from ..resolver import traverse_path
        from .collection import MapOutputCollection

        # Currently support single binding
        if len(bindings) != 1:
            raise NotImplementedError("Multi-field map_over not yet supported")

        field_name, binding = next(iter(bindings.items()))

        # Resolve binding
        root = self._resolver.resolve(binding)

        # Special case: step.* means iterate the MapOutputCollection itself
        if binding.field_path == "*" and isinstance(root, MapOutputCollection):
            value = root
        else:
            value = (
                traverse_path(root, binding.field_path, resolver=self._resolver)
                if binding.field_path
                else root
            )

        if isinstance(value, list):
            return [(i, v, None) for i, v in enumerate(value)]
        elif isinstance(value, dict):
            return [(i, v, k) for i, (k, v) in enumerate(value.items())]
        elif isinstance(value, MapOutputCollection):
            # Support iterating over MapOutputCollection with keys
            return [(i, v, k) for i, (k, v) in enumerate(value.items())]
        else:
            raise TypeError(
                f"map_over field '{field_name}' must resolve to list, dict, or "
                f"MapOutputCollection, got {type(value).__name__}"
            )

    def _prepare_iteration_inputs(
        self,
        index: int,
        value: Any,
        total: int,
        key: str | None = None,
        assigned_model: str | None = None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any], Any]:
        """
        Prepare all inputs for iteration execution.

        Returns:
            Tuple of (iteration_resolver, all_inputs_dict, map_inputs_dict,
            typed_inputs_or_none)

        For map-compatible handlers (with input_type): typed_inputs is
        constructed. For legacy handlers (without input_type): typed_inputs is
        None.

        all_inputs_dict: merged handler_inputs + map_inputs for typed input
        construction. map_inputs_dict: ONLY map_inputs for step override (per
        docstring at line 450-452)
        """
        from ...schemas import MapState
        from ..resolver import traverse_path

        # Create iteration state with optional assigned model
        map_state = MapState(
            iteration_index=index,
            iteration_value=value,
            iteration_key=key,
            iteration_total=total,
            assigned_model=assigned_model,
        )

        # Create resolver with map context
        iter_resolver = self._resolver.with_map_context(map_state)

        # Resolve map_inputs (vary per iteration)
        map_input_values = {}
        for field, binding in self._map_config.map_inputs.items():
            root = iter_resolver.resolve(binding)
            map_input_values[field] = traverse_path(
                root, binding.field_path, resolver=iter_resolver
            )

        # Resolve regular handler_inputs (constant)
        handler_input_values = {}
        for field, binding in self._step.handler_inputs.items():
            root = iter_resolver.resolve(binding)
            handler_input_values[field] = traverse_path(
                root, binding.field_path, resolver=iter_resolver
            )

        # Merge inputs for typed input construction
        all_inputs = {**handler_input_values, **map_input_values}

        # Construct typed input object if handler supports it
        if hasattr(self._handler, "input_type"):
            typed_inputs = self._handler.input_type(**all_inputs)
        else:
            typed_inputs = None

        return iter_resolver, all_inputs, map_input_values, typed_inputs

    async def _execute_iteration(
        self,
        index: int,
        value: Any,
        total: int,
        key: str | None = None,
        iteration_context: dict[int, dict] | None = None,
        assigned_model: str | None = None,
    ) -> "StepOutput":
        """Execute single map iteration."""
        # Prepare inputs
        (
            iter_resolver,
            all_inputs,
            map_inputs,
            typed_inputs,
        ) = self._prepare_iteration_inputs(index, value, total, key, assigned_model)

        # Check checkpoint for this iteration
        iteration_key = f"{self._step.name}:{index}"
        if self._checkpoint_manager:
            fingerprint = (
                typed_inputs.fingerprint()
                if typed_inputs and hasattr(typed_inputs, "fingerprint")
                else None
            )
            cached = await self._checkpoint_manager.load_checkpoint(
                iteration_key,
                input_fingerprint=fingerprint,
            )
            if cached:
                from ...schemas import StepOutput

                return StepOutput(
                    data=cached.output_json
                    if cached.output_json
                    else {"raw": cached.output_raw},
                    metadata=cached.output_meta or {},
                )

        # Execute handler with modified step that has ONLY map_inputs applied
        # All handlers use signature: execute(step, context)
        step_for_iteration = self._create_iteration_step(map_inputs, assigned_model)

        # After creating step_for_iteration, update context with model info
        if iteration_context is not None and index in iteration_context:
            model_ref = getattr(step_for_iteration, "model_ref", None)
            if model_ref:
                iteration_context[index]["model_id"] = model_ref

        # Emit iteration started event
        pipeline_id, execution_id = self._get_event_context()
        ctx = iteration_context.get(index, {}) if iteration_context else {}
        model_id = ctx.get("model_id")
        gateway_id = ctx.get("gateway_id")
        self._publish_event(
            MapIterationStarted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=self._step.name,
                iteration_index=index,
                model_id=model_id,
                gateway_id=gateway_id,
            )
        )

        # Get pre-generated map_iteration_request_id from iteration_context
        map_iteration_request_id = None
        if iteration_context and index in iteration_context:
            map_iteration_request_id = iteration_context[index].get(
                "map_iteration_request_id"
            )

        # Create runtime context with map_iteration_request_id for this iteration
        iter_runtime = self._runtime
        if map_iteration_request_id:
            iter_runtime = self._runtime.with_map_iteration_request_id(
                map_iteration_request_id
            )

        # Populate map iteration state for provenance tracking
        source_step_name = self._extract_source_step_name()
        if source_step_name:
            from ...handlers.protocol import MapIterationState

            map_state = MapIterationState(
                source_step_name=source_step_name,
                iteration_key=key,
                iteration_index=index,
            )
            iter_runtime = iter_runtime.with_map_state(map_state)

        # Execute handler with iteration-specific context
        output = await self._handler.execute(step_for_iteration, iter_runtime)
        # Save checkpoint
        if self._checkpoint_manager and self._checkpoint_manager.should_checkpoint(
            self._step
        ):
            fingerprint = (
                typed_inputs.fingerprint()
                if typed_inputs and hasattr(typed_inputs, "fingerprint")
                else None
            )
            # Pass StepOutput directly - manager will extract checkpoint data
            await self._checkpoint_manager.save_checkpoint(
                iteration_key,
                output,
                input_fingerprint=fingerprint,
            )

        logger.debug("[%s] Iteration %d/%d complete", self._step.name, index + 1, total)
        return output

    def _create_iteration_step(
        self, map_inputs: dict[str, Any], assigned_model: str | None = None
    ) -> "StepConfig":
        """
        Create step config with map_inputs applied as overrides.

        For legacy handlers that use step.model_ref etc., this allows
        map_inputs like {model_ref: mapNs.iteration.value} to override
        the step's static model_ref per iteration.

        Template placeholder values (not step config fields) are stored in
        resolved_map_inputs for use by _build_prompt_context.

        Special handling for generation_parameters: merges with step-level
        params instead of replacing (allows schema at step level, params in map).

        If assigned_model provided and model_ref not in map_inputs,
        applies pool-assigned model as model_ref override.
        """
        # Separate step config overrides from template inputs
        step_overrides = {}
        template_inputs = {}

        for field, value in map_inputs.items():
            # Check if field is a step config attribute
            if hasattr(self._step, field) and field != "resolved_map_inputs":
                # Special case: merge generation_parameters instead of replace
                if field == "generation_parameters" and isinstance(value, dict):
                    base_params = getattr(self._step, field, {}) or {}
                    merged_params = {**base_params, **value}
                    step_overrides[field] = merged_params
                    logger.debug(
                        "[%s] Merging step.%s: base=%r + override=%r = %r",
                        self._step.name,
                        field,
                        base_params,
                        value,
                        merged_params,
                    )
                else:
                    step_overrides[field] = value
                    logger.debug(
                        "[%s] Overriding step.%s = %r for iteration",
                        self._step.name,
                        field,
                        value,
                    )
            else:
                # Template placeholder value - store for handler use
                template_inputs[field] = value
                logger.debug(
                    "[%s] Template input %s = %r for iteration",
                    self._step.name,
                    field,
                    value,
                )

        # Apply pool-assigned model if not explicitly overridden
        if assigned_model and "model_ref" not in step_overrides:
            step_overrides["model_ref"] = assigned_model
            logger.debug(
                "[%s] Using pool-assigned model_ref=%r",
                self._step.name,
                assigned_model,
            )

        # Store template inputs in resolved_map_inputs
        if template_inputs:
            step_overrides["resolved_map_inputs"] = template_inputs

        if step_overrides:
            return self._step.model_copy(update=step_overrides)
        return self._step
