"""Periodic reconciliation loop lifecycle and single-pass orchestration."""

from __future__ import annotations

import asyncio
import contextlib

from universal_logging import get_logger

from .constants import RECONCILE_INTERVAL_S, VRAM_DISCREPANCY_THRESHOLD_MB

logger = get_logger(__name__)


class ReconcileLoopMixin:
    """Start/stop the background task and run one full reconciliation pass."""

    async def start(self) -> None:
        """Start periodic reconciliation loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._reconcile_loop(), name="vram-reconciler")
        logger.info(
            "VRAM reconciler started (interval=%ss, threshold=%sMB)",
            RECONCILE_INTERVAL_S,
            VRAM_DISCREPANCY_THRESHOLD_MB,
        )

    async def stop(self) -> None:
        """Stop reconciliation loop."""
        if self._task is None:
            return
        _ = self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("VRAM reconciler stopped")

    async def _reconcile_loop(self) -> None:
        while True:
            await asyncio.sleep(RECONCILE_INTERVAL_S)
            try:
                await self._reconcile_once()
            except Exception:
                logger.error("VRAM reconciliation failed", exc_info=True)
                await asyncio.sleep(RECONCILE_INTERVAL_S / 2)

    async def _reconcile_once(self) -> None:
        tracked_models = set(self._resource_tracker.get_loaded_models())
        running_processes = self._worker_controller.get_running_worker_processes()

        transitioning = self._get_transitioning_models()
        phantom_models = sorted(set(running_processes) - tracked_models - transitioning)
        for model_id in phantom_models:
            pid = running_processes.get(model_id)
            tracker_status = self._get_tracker_status(model_id)
            logger.warning(
                "Phantom model detected: %s (pid=%s, tracker_status=%s). "
                "Attempting force cleanup.",
                model_id,
                pid,
                tracker_status,
            )
            await self._emit_phantom_detected(model_id, tracker_status)
            await self._force_cleanup(model_id, pid)

        await self._sweep_ghost_models(tracked_models, running_processes)

        remaining_tracked = set(self._resource_tracker.get_loaded_models())
        await self._check_vram_discrepancy(remaining_tracked)
