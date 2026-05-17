"""Fail-fast execution mode for map steps.

Implements the "cancel as soon as success threshold becomes impossible"
strategy. The loop never short-circuits on success (more data is always
better); it only cancels pending work when even all remaining iterations
succeeding would still leave the required count unreachable. Delegates
threshold arithmetic, result collection, gateway analysis, and output
ordering to the focused helper modules.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..iteration_state import IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput
    from .events import MapEventPublisher
    from .iteration_result_collector import IterationResultCollector

from universal_logging import get_logger

from . import gateway_contention, iteration_output_ordering, threshold_policy

logger = get_logger(__name__)


class FailFastExecutionMode:
    """Owns the fail-fast execution path (execute_with_fail_fast).

    No inference-timeout monitor is used in this path. Cancellation of
    pending tasks is performed directly; cancel_pending_iterations on the
    concurrency manager is intentionally not called (historical behavior).
    """

    def __init__(
        self,
        step: StepConfig,
        event_publisher: MapEventPublisher,
        iteration_result_collector: IterationResultCollector,
    ) -> None:
        """Store the minimal collaborators required for fail-fast."""
        self._step = step
        self._event_publisher = event_publisher
        self._iteration_result_collector = iteration_result_collector

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
        required = threshold_policy.compute_required_success_count(total, threshold)
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

                iteration_results, ff_results = (
                    self._iteration_result_collector.collect_iteration_results(
                        done=done,
                        cancelled=pending,
                        tasks=tasks,
                        iteration_context=iteration_context,
                        timeout_status=IterationStatus.CANCELLED,
                        timeout_duration=None,
                    )
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
                    gateway_serialization=gateway_contention.serialized_gateways(
                        iteration_results
                    ),
                )

        # All completed normally
        iteration_results, results_by_index = (
            self._iteration_result_collector.collect_iteration_results(
                done=done,
                cancelled=set(),
                tasks=tasks,
                iteration_context=iteration_context,
                timeout_status=IterationStatus.CANCELLED,
                timeout_duration=None,
            )
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

        if not threshold_policy.success_count_meets_threshold(
            success_count, total, threshold
        ):
            raise MapPartialFailureError(
                step_name=self._step.name,
                completed_count=success_count,
                failed_count=len(iteration_results) - success_count,
                total_count=total,
                threshold=threshold if threshold is not None else total,
                timeout_seconds=None,
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
