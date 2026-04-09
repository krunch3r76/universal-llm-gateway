"""Map execution modes: timeout, fail-fast, and result collection."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from ..iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput
    from .concurrency_manager import MapConcurrencyManager
    from .events import MapEventPublisher

logger = logging.getLogger(__name__)


class MapExecutionModes:
    """Implements timeout, fail-fast, and result collection for map steps."""

    def __init__(
        self,
        step: StepConfig,
        event_publisher: MapEventPublisher,
        concurrency_manager: MapConcurrencyManager,
    ) -> None:
        self._step = step
        self._event_publisher = event_publisher
        self._concurrency_manager = concurrency_manager

    def success_count_meets_threshold(
        self,
        success_count: int,
        total: int,
        threshold: int | float | None,
    ) -> bool:
        """Check if success count meets the configured threshold."""
        if threshold is None:
            return success_count == total
        if isinstance(threshold, int):
            return success_count >= threshold
        return (success_count / total) >= threshold if total > 0 else True

    def compute_required_success_count(
        self, total: int, threshold: int | float | None
    ) -> int:
        """Compute required success count from threshold configuration."""
        if threshold is None:
            return total
        if isinstance(threshold, int):
            return threshold
        return math.ceil(total * threshold)

    @staticmethod
    def _serialized_gateways(
        iteration_results: list[IterationResult],
    ) -> tuple[str, ...] | None:
        """Return gateways with repeated failures/timeouts (contention signal)."""
        gateway_counts = Counter(
            r.gateway_id for r in iteration_results if r.gateway_id
        )
        serialized_gateways = tuple(
            gw for gw, count in gateway_counts.items() if count > 1
        )
        return serialized_gateways if serialized_gateways else None

    def collect_iteration_results(
        self,
        done: set[asyncio.Task[Any]],
        cancelled: set[asyncio.Task[Any]],
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
        timeout_status: IterationStatus,
        timeout_duration: float | None,
    ) -> tuple[list[IterationResult], dict[int, StepOutput]]:
        """
        Collect IterationResult and output index from completed and cancelled tasks.

        cancelled tasks are assigned timeout_status (TIMEOUT or CANCELLED).
        """
        iteration_results: list[IterationResult] = []
        results_by_index: dict[int, StepOutput] = {}

        for task in done:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            started_at = ctx.get("started_at")
            completion_time = ctx.get("completed_at", time.monotonic())
            duration = (completion_time - started_at) if started_at else None

            if task.cancelled():
                # Inference timeout monitor cancelled this task mid-flight
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=timeout_status,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        started_at=started_at,
                    )
                )
                logger.warning(
                    "[%s] Iteration %d cancelled by inference timeout monitor",
                    self._step.name,
                    idx,
                )
            elif task.exception() is not None:
                exc = task.exception()
                from ....dag import ResponseTruncatedError

                truncated_response = None
                truncation_tokens = None
                if isinstance(exc, ResponseTruncatedError):
                    from pathlib import Path

                    truncation_tokens = exc.completion_tokens
                    dump_dir = Path("/tmp/pipeline-truncated")
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    dump_file = dump_dir / f"{self._step.name}-iter{idx}-{int(time.monotonic() * 1000)}.txt"
                    try:
                        dump_file.write_text(exc.response_preview, encoding="utf-8")
                        truncated_response = str(dump_file)
                    except OSError:
                        truncated_response = exc.response_preview[:500]
                iteration_results.append(
                    IterationResult(
                        index=idx,
                        status=IterationStatus.FAILED,
                        model_id=ctx.get("model_id"),
                        gateway_id=ctx.get("gateway_id"),
                        duration_seconds=duration,
                        error_message=str(exc),
                        started_at=started_at,
                        truncated_response=truncated_response,
                        truncation_tokens=truncation_tokens,
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

        for task in cancelled:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            iteration_results.append(
                IterationResult(
                    index=idx,
                    status=timeout_status,
                    model_id=ctx.get("model_id"),
                    gateway_id=ctx.get("gateway_id"),
                    duration_seconds=timeout_duration,
                    started_at=ctx.get("started_at"),
                )
            )

        return iteration_results, results_by_index

    async def _inference_timeout_monitor(
        self,
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
        inference_timeout: float,
    ) -> None:
        """
        Background monitor that cancels iterations exceeding inference timeout.

        Only cancels iterations where at least one inference boundary signal has
        arrived. Priority order for timeout start:
          1. inference_started_at (request.inference.started — primary)
          2. fallback_boundary_at (request.processing — conservative estimate)
        Queued iterations (neither signal set) are exempt — queue wait is
        unbounded within the outer wall-clock guard.
        """
        task_by_idx: dict[int, asyncio.Task[Any]] = {
            idx: task for task, idx in tasks.items()
        }
        check_interval = min(inference_timeout / 4, 5.0)
        while True:
            await asyncio.sleep(check_interval)
            now = time.monotonic()
            for idx, ctx in iteration_context.items():
                inference_started = ctx.get("inference_started_at") or ctx.get(
                    "fallback_boundary_at"
                )
                if inference_started is None:
                    continue
                if "completed_at" in ctx:
                    continue
                elapsed = now - inference_started
                if elapsed <= inference_timeout:
                    continue
                task = task_by_idx.get(idx)
                if task and not task.done():
                    logger.warning(
                        "[%s] Iteration %d inference timeout: %.1fs > %.1fs",
                        self._step.name,
                        idx,
                        elapsed,
                        inference_timeout,
                    )
                    task.cancel()

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
                self._inference_timeout_monitor(
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

        iteration_results, results_by_index = self.collect_iteration_results(
            done=done,
            cancelled=pending,
            tasks=tasks,
            iteration_context=iteration_context,
            timeout_status=IterationStatus.TIMEOUT,
            timeout_duration=timeout_seconds,
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

        if not self.success_count_meets_threshold(completed, total, threshold):
            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=completed,
                failed_count=failed,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=timeout_seconds,
                iteration_results=tuple(iteration_results),
                gateway_serialization=self._serialized_gateways(iteration_results),
            )

        outputs = []
        output_keys = []
        output_positions = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)
                output_positions.append(idx)

        return outputs, output_keys, output_positions

    async def execute_with_fail_fast(
        self,
        tasks: dict[asyncio.Task[Any], int],
        total: int,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict[str, Any]],
    ) -> tuple[list[StepOutput], list[str | None], list[int]]:
        """
        Execute with fail-fast on impossible threshold.

        Cancels remaining as soon as failures prove threshold is unreachable.
        Does NOT stop early on success — more results are always better.
        """
        from ...errors import MapPartialFailureError

        done: set[asyncio.Task[Any]] = set()
        pending = set(tasks.keys())
        required = self.compute_required_success_count(total, threshold)
        key_by_idx = dict(iteration_metadata)

        while pending:
            newly_done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            done.update(newly_done)

            success_count = sum(1 for t in done if t.exception() is None)
            failure_count = len(done) - success_count
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
                for task in pending:
                    task.cancel()

                iteration_results, ff_results = self.collect_iteration_results(
                    done=done,
                    cancelled=pending,
                    tasks=tasks,
                    iteration_context=iteration_context,
                    timeout_status=IterationStatus.CANCELLED,
                    timeout_duration=None,
                )
                iteration_results.sort(key=lambda r: r.index)
                self._event_publisher.emit_iteration_events(
                    iteration_results, ff_results, key_by_idx
                )

                raise MapPartialFailureError(
                    step_name=self._step.name,
                    completed_count=success_count,
                    failed_count=failure_count + len(pending),
                    total_count=total,
                    threshold=threshold if threshold is not None else total,
                    timeout_seconds=None,
                    iteration_results=tuple(iteration_results),
                    gateway_serialization=self._serialized_gateways(iteration_results),
                )

        # All completed normally
        iteration_results, results_by_index = self.collect_iteration_results(
            done=done,
            cancelled=set(),
            tasks=tasks,
            iteration_context=iteration_context,
            timeout_status=IterationStatus.CANCELLED,
            timeout_duration=None,
        )
        iteration_results.sort(key=lambda r: r.index)
        success_count = len(results_by_index)

        self._event_publisher.emit_iteration_events(
            iteration_results, results_by_index, key_by_idx
        )

        logger.info(
            "[%s] Fail-fast complete: %d/%d succeeded",
            self._step.name,
            success_count,
            total,
        )

        if not self.success_count_meets_threshold(success_count, total, threshold):
            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=success_count,
                failed_count=len(iteration_results) - success_count,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=None,
                iteration_results=tuple(iteration_results),
                gateway_serialization=self._serialized_gateways(iteration_results),
            )

        outputs = []
        output_keys = []
        output_positions = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)
                output_positions.append(idx)

        return outputs, output_keys, output_positions
