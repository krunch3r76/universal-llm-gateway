"""Map executor concurrency: timeout monitoring and federation cancellation."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ....events.map import MapTimeoutWarning

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from ....schemas import StepConfig
    from .events import MapEventPublisher

logger = logging.getLogger(__name__)


class MapConcurrencyManager:
    """Manages timeout warnings and federation request cancellation."""

    def __init__(
        self,
        step: StepConfig,
        cancel_callback: Callable[[str, str | None], Awaitable[bool]] | None,
        event_publisher: MapEventPublisher,
    ) -> None:
        self._step = step
        self._cancel_callback = cancel_callback
        self._event_publisher = event_publisher

    async def timeout_warning_monitor(
        self,
        timeout_seconds: float,
        tasks: dict[asyncio.Task[Any], int],
        start_time: float,
    ) -> None:
        """Emit warnings when stall timeout approaches without progress.

        Tracks time since last iteration completion. Warns at 75% and 90% of
        the stall timeout window. Resets when new completions are detected.
        """
        pipeline_id, execution_id = self._event_publisher.get_event_context()
        warned_75 = False
        warned_90 = False
        last_completed_count = 0
        last_progress_time = start_time

        while True:
            await asyncio.sleep(5.0)
            now = time.monotonic()

            pending_indices = [idx for task, idx in tasks.items() if not task.done()]
            completed = len(tasks) - len(pending_indices)

            if not pending_indices:
                break

            if completed > last_completed_count:
                last_completed_count = completed
                last_progress_time = now
                warned_75 = False
                warned_90 = False

            stall_elapsed = now - last_progress_time
            stall_pct = stall_elapsed / timeout_seconds

            if stall_pct >= 0.75 and not warned_75:
                warned_75 = True
                self._event_publisher.publish_event(
                    MapTimeoutWarning(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        elapsed_seconds=stall_elapsed,
                        timeout_seconds=timeout_seconds,
                        pending_iterations=pending_indices,
                        completed_iterations=completed,
                    )
                )
                logger.warning(
                    "[%s] Stall warning: %.0fs without progress "
                    "(%.0f%% of stall timeout, %d/%d pending)",
                    self._step.name,
                    stall_elapsed,
                    stall_pct * 100,
                    len(pending_indices),
                    len(tasks),
                )

            if stall_pct >= 0.90 and not warned_90:
                warned_90 = True
                self._event_publisher.publish_event(
                    MapTimeoutWarning(
                        pipeline_id=pipeline_id,
                        execution_id=execution_id,
                        step_name=self._step.name,
                        elapsed_seconds=stall_elapsed,
                        timeout_seconds=timeout_seconds,
                        pending_iterations=pending_indices,
                        completed_iterations=completed,
                    )
                )
                logger.warning(
                    "[%s] Stall warning: %.0fs without progress "
                    "(%.0f%% of stall timeout, %d/%d pending)",
                    self._step.name,
                    stall_elapsed,
                    stall_pct * 100,
                    len(pending_indices),
                    len(tasks),
                )

    async def cancel_pending_iterations(
        self,
        pending: set[asyncio.Task[Any]],
        tasks: dict[asyncio.Task[Any], int],
        iteration_context: dict[int, dict[str, Any]],
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
