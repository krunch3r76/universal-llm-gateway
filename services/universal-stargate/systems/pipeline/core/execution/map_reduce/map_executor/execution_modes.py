"""Map execution modes facade.

This module preserves the original public import surface and exact method
signatures expected by executor.py and test_execution_modes.py.

All behavior has been delegated to focused, single-responsibility modules:
- threshold_policy
- gateway_contention
- iteration_output_ordering
- iteration_result_collector
- inference_timeout_monitor
- timeout_execution_mode
- fail_fast_execution_mode

MapExecutionModes is now a thin compatibility layer that constructs the
collaborators and forwards calls. The _inference_timeout_monitor method
remains as an async wrapper so that existing tests that call it directly
continue to work without modification.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..iteration_state import IterationResult, IterationStatus

if TYPE_CHECKING:
    from ....schemas import StepConfig, StepOutput
    from .concurrency_manager import MapConcurrencyManager
    from .events import MapEventPublisher

# Focused collaborators (imported for construction and delegation)
# Policy / helper modules for the thin delegation methods
from . import gateway_contention, threshold_policy
from .fail_fast_execution_mode import FailFastExecutionMode
from .inference_timeout_monitor import InferenceTimeoutMonitor
from .iteration_result_collector import IterationResultCollector
from .timeout_execution_mode import TimeoutExecutionMode


class MapExecutionModes:
    """Compatibility facade for map execution strategies.

    Consumers continue to import MapExecutionModes from this module and
    call the same seven methods with the same signatures. All heavy
    lifting is performed by the injected collaborator objects.
    """

    def __init__(
        self,
        step: StepConfig,
        event_publisher: MapEventPublisher,
        concurrency_manager: MapConcurrencyManager,
    ) -> None:
        """Construct the original three references plus all focused helpers."""
        self._step = step
        self._event_publisher = event_publisher
        self._concurrency_manager = concurrency_manager

        # Result collector (used by both execution paths)
        self._result_collector = IterationResultCollector(step)

        # Inference monitor (only used by timeout path, but always available)
        self._monitor = InferenceTimeoutMonitor(step, concurrency_manager)

        # The two execution strategies
        self._timeout_mode = TimeoutExecutionMode(
            step,
            event_publisher,
            concurrency_manager,
            self._result_collector,
            self._monitor,
        )
        self._fail_fast_mode = FailFastExecutionMode(
            step, event_publisher, self._result_collector
        )

    def success_count_meets_threshold(
        self,
        success_count: int,
        total: int,
        threshold: int | float | None,
    ) -> bool:
        """Check if success count meets the configured threshold."""
        return threshold_policy.success_count_meets_threshold(
            success_count, total, threshold
        )

    def compute_required_success_count(
        self, total: int, threshold: int | float | None
    ) -> int:
        """Compute required success count from threshold configuration."""
        return threshold_policy.compute_required_success_count(total, threshold)

    @staticmethod
    def _serialized_gateways(
        iteration_results: list[IterationResult],
    ) -> tuple[str, ...] | None:
        """Return gateways with repeated failures/timeouts (contention signal)."""
        return gateway_contention.serialized_gateways(iteration_results)

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
        return self._result_collector.collect_iteration_results(
            done=done,
            cancelled=cancelled,
            tasks=tasks,
            iteration_context=iteration_context,
            timeout_status=timeout_status,
            timeout_duration=timeout_duration,
        )

    async def _inference_timeout_monitor(
        self,
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
        inference_timeout: float,
    ) -> None:
        """
        Background monitor that cancels iterations exceeding inference timeout.

        This method is retained on the facade (with its original signature)
        so that test_execution_modes.py can continue to call
        modes._inference_timeout_monitor(...) directly. It simply forwards
        to the real implementation inside InferenceTimeoutMonitor.
        """
        await self._monitor.monitor_inference_timeouts(
            tasks, iteration_context, inference_timeout
        )

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

        Delegates to TimeoutExecutionMode after the collaborators were
        wired in __init__.
        """
        return await self._timeout_mode.execute_with_timeout(
            tasks,
            total,
            timeout_seconds,
            threshold,
            iteration_metadata,
            iteration_context,
            inference_timeout_seconds=inference_timeout_seconds,
        )

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

        Delegates to FailFastExecutionMode.
        """
        return await self._fail_fast_mode.execute_with_fail_fast(
            tasks,
            total,
            threshold,
            iteration_metadata,
            iteration_context,
        )
