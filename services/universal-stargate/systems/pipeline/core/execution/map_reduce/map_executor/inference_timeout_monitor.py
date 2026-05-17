"""Inference timeout monitor for per-iteration guard in map execution.

Owns the background loop that periodically checks whether any in-flight
iteration has exceeded the configured inference_timeout_seconds measured
from the first inference boundary signal. When a violation is detected the
task is cancelled and cancel_pending_iterations is invoked so that the
federation side can clean up.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....schemas import StepConfig
    from .concurrency_manager import MapConcurrencyManager

from universal_logging import get_logger

logger = get_logger(__name__)


class InferenceTimeoutMonitor:
    """Background inference-duration guard for map iterations.

    The monitor is started (as an asyncio.Task) only when the map step
    declares inference_timeout_seconds. It is cancelled from the outer
    execute_with_timeout finally block. All decision logic and the call
    to the concurrency manager live inside monitor_inference_timeouts.
    """

    def __init__(
        self,
        step: StepConfig,
        concurrency_manager: MapConcurrencyManager,
    ) -> None:
        """Store step (for logging) and the manager that performs cancellation."""
        self._step = step
        self._concurrency_manager = concurrency_manager

    async def monitor_inference_timeouts(
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
            timed_out_tasks: set[asyncio.Task[Any]] = set()
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
                    timed_out_tasks.add(task)
            if timed_out_tasks:
                await self._concurrency_manager.cancel_pending_iterations(
                    timed_out_tasks, tasks, iteration_context
                )
