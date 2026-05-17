"""Timeout-based execution mode for map steps.

Implements the stall-aware outer timeout plus optional per-inference
timeout guard. On timeout or client cancellation it coordinates cleanup,
collects partial results, emits events, and raises MapPartialFailureError
when the success threshold is not met. Delegates threshold checks,
gateway contention detection, result collection, and output ordering to
the focused collaborators.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from ..iteration_state import IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput
    from .concurrency_manager import MapConcurrencyManager
    from .events import MapEventPublisher
    from .inference_timeout_monitor import InferenceTimeoutMonitor
    from .iteration_result_collector import IterationResultCollector

from universal_logging import get_logger

from . import gateway_contention, iteration_output_ordering, threshold_policy

logger = get_logger(__name__)


class TimeoutExecutionMode:
    """Owns the stall-timeout execution path (execute_with_timeout).

    Collaborators are injected at construction so the mode itself stays
    focused on the async control flow, monitor lifecycle, cancellation
    ordering, and the decision of whether to raise MapPartialFailureError.
    """

    def __init__(
        self,
        step: StepConfig,
        event_publisher: MapEventPublisher,
        concurrency_manager: MapConcurrencyManager,
        iteration_result_collector: IterationResultCollector,
        inference_timeout_monitor: InferenceTimeoutMonitor,
    ) -> None:
        """Store all collaborators required by the timeout execution path."""
        self._step = step
        self._event_publisher = event_publisher
        self._concurrency_manager = concurrency_manager
        self._iteration_result_collector = iteration_result_collector
        self._inference_timeout_monitor = inference_timeout_monitor

    async def execute_with_timeout(
        self,
        tasks: dict[asyncio.Task[Any], int],
        total: int,
        timeout_seconds: float,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict[str, Any]],
        inference_timeout_seconds: float | None = None,
    ) -> tuple[list[StepOutput], list[str | None], list[int]]:
        """
        Execute with stall-aware timeout and optional partial success.

        Two timeout layers:
        - timeout_seconds: stall timeout — resets on each iteration completion.
          The batch is only cancelled when no iteration completes within this
          window, preventing false timeouts on large batches where individual
          iterations complete at different rates.
        - inference_timeout_seconds: per-iteration guard from inference start

        On CancelledError (client disconnect), cancels all pending federation
        requests before propagating the exception.
        """
        from ...errors import MapPartialFailureError

        start_time = time.monotonic()

        monitor_task = asyncio.create_task(
            self._concurrency_manager.timeout_warning_monitor(
                timeout_seconds, tasks, start_time
            )
        )

        inference_monitor: asyncio.Task[None] | None = None
        if inference_timeout_seconds is not None:
            inference_monitor = asyncio.create_task(
                self._inference_timeout_monitor.monitor_inference_timeouts(
                    tasks, iteration_context, inference_timeout_seconds
                )
            )

        try:
            deadline = time.monotonic() + timeout_seconds
            done: set[asyncio.Task[Any]] = set()
            pending = set(tasks.keys())

            while pending:
                remaining = max(0.0, deadline - time.monotonic())
                newly_done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if newly_done:
                    done.update(newly_done)
                    deadline = time.monotonic() + timeout_seconds
                else:
                    break  # stalled — no completion within timeout window
        except asyncio.CancelledError:
            logger.info(
                "[%s] Cancelled during execution, cancelling %d federation requests",
                self._step.name,
                len(tasks),
            )
            pending_tasks = {t for t in tasks if not t.done()}
            for task in pending_tasks:
                task.cancel()
            await self._concurrency_manager.cancel_pending_iterations(
                pending_tasks, tasks, iteration_context
            )
            raise
        finally:
            monitor_task.cancel()
            if inference_monitor:
                inference_monitor.cancel()
            monitors = [monitor_task]
            if inference_monitor:
                monitors.append(inference_monitor)
            for m in monitors:
                try:
                    await m
                except asyncio.CancelledError:
                    pass

        for task in pending:
            task.cancel()
        await self._concurrency_manager.cancel_pending_iterations(
            pending, tasks, iteration_context
        )

        if pending:
            logger.warning(
                "[%s] Batch stalled: %d pending after %.1fs without progress",
                self._step.name,
                len(pending),
                timeout_seconds,
            )

        iteration_results, results_by_index = (
            self._iteration_result_collector.collect_iteration_results(
                done=done,
                cancelled=pending,
                tasks=tasks,
                iteration_context=iteration_context,
                timeout_status=IterationStatus.TIMEOUT,
                timeout_duration=timeout_seconds,
            )
        )
        for task in pending:
            idx = tasks[task]
            logger.warning(
                "[%s] Iteration %d timed out (stall_timeout)",
                self._step.name,
                idx,
            )
        iteration_results.sort(key=lambda r: r.index)

        key_by_idx = dict(iteration_metadata)
        self._event_publisher.emit_iteration_events(
            iteration_results, results_by_index, key_by_idx
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

        if not threshold_policy.success_count_meets_threshold(
            completed, total, threshold
        ):
            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=completed,
                failed_count=failed,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=timeout_seconds,
                iteration_results=tuple(iteration_results),
                gateway_serialization=gateway_contention.serialized_gateways(
                    iteration_results
                ),
            )

        outputs, output_keys, output_positions = (
            iteration_output_ordering.ordered_successful_outputs(
                iteration_metadata, results_by_index
            )
        )

        return outputs, output_keys, output_positions
