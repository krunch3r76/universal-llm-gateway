"""Phantom model detection events and force-unload cleanup for orphan workers."""

from __future__ import annotations

from universal_logging import get_logger

from ..hardware import get_vram_info

logger = get_logger(__name__)


class PhantomCleanupMixin:
    """Detect orphan worker processes and force-unload or kill them."""

    async def _emit_phantom_detected(
        self, model_id: str, tracker_status: str | None
    ) -> None:
        if self._event_bus is None:
            logger.warning(
                "event_bus unavailable - phantom detection event not published (model_id=%s)",
                model_id,
            )
            return
        try:
            from ...events.types import PhantomModelDetected

            await self._event_bus.publish_nowait(
                PhantomModelDetected(
                    model_id=model_id,
                    process_status="running",
                    tracker_status=tracker_status,
                )
            )
        except Exception:
            logger.error(
                "Failed to publish phantom detection event for %s",
                model_id,
                exc_info=True,
            )

    async def _force_cleanup(self, model_id: str, pid: int | None) -> None:
        success = False
        vram_freed_mb: int | None = None
        try:
            before = get_vram_info()["available_vram_mb"]
            result = await self._worker_controller.unload_model(model_id, force=True)
            success = result.success
            after = get_vram_info()["available_vram_mb"]
            vram_freed_mb = max(0, after - before)
            if success:
                logger.info(
                    "Phantom model %s force-unloaded (freed=%sMB)",
                    model_id,
                    vram_freed_mb,
                )
            else:
                logger.error(
                    "Phantom model %s force-unload failed: %s",
                    model_id,
                    result.reason,
                )
                if pid is not None:
                    success = await self._kill_worker_process(model_id, pid)
        except Exception as e:
            logger.error(
                "Exception during phantom cleanup for %s: %s",
                model_id,
                e,
                exc_info=True,
            )
            if pid is not None:
                success = await self._kill_worker_process(model_id, pid)
            else:
                success = False
        finally:
            await self._emit_phantom_cleaned(model_id, success, vram_freed_mb)

    async def _emit_phantom_cleaned(
        self, model_id: str, success: bool, vram_freed_mb: int | None
    ) -> None:
        if self._event_bus is None:
            logger.warning(
                "event_bus unavailable - phantom cleanup event not published (model_id=%s)",
                model_id,
            )
            return
        try:
            from ...events.types import PhantomModelCleaned

            await self._event_bus.publish_nowait(
                PhantomModelCleaned(
                    model_id=model_id,
                    success=success,
                    vram_freed_mb=vram_freed_mb,
                )
            )
        except Exception:
            logger.error(
                "Failed to publish phantom cleanup event for %s",
                model_id,
                exc_info=True,
            )
