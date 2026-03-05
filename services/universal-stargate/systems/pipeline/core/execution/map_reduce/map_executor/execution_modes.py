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
        results_by_index: dict[int, Any] = {}

        for task in done:
            idx = tasks[task]
            ctx = iteration_context.get(idx, {})
            started_at = ctx.get("started_at")
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

    async def execute_with_timeout(
        self,
        tasks: dict[asyncio.Task[Any], int],
        total: int,
        timeout_seconds: float,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict[str, Any]],
    ) -> tuple[list[StepOutput], list[str | None]]:
        """
        Execute with timeout and optional partial success.

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

        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                timeout=timeout_seconds,
                return_when=asyncio.ALL_COMPLETED,
            )
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
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        for task in pending:
            task.cancel()
        await self._concurrency_manager.cancel_pending_iterations(
            pending, tasks, iteration_context
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
            logger.warning("[%s] Iteration %d timed out", self._step.name, tasks[task])
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

        outputs = []
        output_keys = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)

        return outputs, output_keys

    async def execute_with_fail_fast(
        self,
        tasks: dict[asyncio.Task[Any], int],
        total: int,
        threshold: int | float | None,
        iteration_metadata: list[tuple[int, str | None]],
        iteration_context: dict[int, dict[str, Any]],
    ) -> tuple[list[StepOutput], list[str | None]]:
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

        outputs = []
        output_keys = []
        for idx, key in iteration_metadata:
            if idx in results_by_index:
                outputs.append(results_by_index[idx])
                output_keys.append(key)

        return outputs, output_keys
