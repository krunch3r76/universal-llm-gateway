"""Ghost model sweep for tracked models whose inference engine subprocess died."""

from __future__ import annotations

from universal_logging import get_logger

from ..hardware import get_vram_info

logger = get_logger(__name__)


class GhostSweepMixin:
    """Unload ghost models and emit MODEL_UNLOADED plus cleanup events."""

    async def _sweep_ghost_models(
        self,
        tracked_models: set[str],
        running_processes: dict[str, int],
    ) -> None:
        """For each tracked model with a running worker, verify the engine is alive.

        ∀ model ∈ tracked ∩ running: check engine PID (fast) or RPC health (fallback).
        Engine PID dead ⟹ ghost. Cleanup: force-unload, update tracker, emit MODEL_UNLOADED.
        """
        candidates = sorted(tracked_models & set(running_processes))
        for model_id in candidates:
            engine_pid = self._worker_controller.get_engine_pid(model_id)
            healthy = True
            if engine_pid is not None:
                if not self._is_engine_pid_alive(engine_pid):
                    logger.warning(
                        "Engine PID %d dead for %s — ghost detected (no RPC needed)",
                        engine_pid,
                        model_id,
                    )
                    healthy = False
            if healthy:
                try:
                    healthy = await self._worker_controller.check_engine_health(
                        model_id
                    )
                except Exception:
                    logger.warning(
                        "Engine health check failed for %s, treating as ghost",
                        model_id,
                        exc_info=True,
                    )
                    healthy = False

            if healthy:
                continue

            pid = running_processes.get(model_id)
            vram_mb = self._get_tracked_vram(model_id)
            logger.error(
                "🔍 Ghost model detected: %s — tracked as loaded but engine dead "
                "(worker_pid=%s, phantom_vram=%sMB). Cleaning up.",
                model_id,
                pid,
                vram_mb,
            )
            await self._cleanup_ghost_model(model_id, pid)

    async def _cleanup_ghost_model(self, model_id: str, pid: int | None) -> None:
        """Unload a ghost model: force-unload worker, update tracker, emit events."""
        vram_freed_mb: int | None = None
        success = False
        try:
            before = get_vram_info()["available_vram_mb"]
            result = await self._worker_controller.unload_model(model_id, force=True)
            success = result.success
            after = get_vram_info()["available_vram_mb"]
            vram_freed_mb = max(0, after - before)
        except Exception as e:
            logger.error(
                "Ghost model cleanup exception for %s: %s", model_id, e, exc_info=True
            )
            if pid is not None:
                success = await self._kill_worker_process(model_id, pid)

        if not success and pid is not None:
            success = await self._kill_worker_process(model_id, pid)

        self._clear_ghost_from_tracker(model_id)

        await self._emit_model_unloaded(model_id)

        try:
            await self._resource_tracker.get_system_resources()
        except Exception:
            logger.warning("Failed to publish resource update after ghost cleanup")

        await self._emit_ghost_cleaned(model_id, success, vram_freed_mb)

        logger.info(
            "✅ Ghost model %s cleaned up (success=%s, vram_freed=%sMB)",
            model_id,
            success,
            vram_freed_mb,
        )

    def _clear_ghost_from_tracker(self, model_id: str) -> None:
        """Mark ghost model as NOT_LOADED via domain verb, clear resources."""
        try:
            self._resource_tracker.set_model_not_loaded(
                model_id, "ghost_cleared_by_vram_reconciler"
            )
            info = self._resource_tracker._models.get(model_id)
            if info is not None:
                info.vram_usage_mb = 0
                info.measured_vram_mb = None
                info.ram_usage_mb = 0
            logger.info("Resource tracker cleared for ghost model %s", model_id)
        except Exception:
            logger.error(
                "Failed to clear tracker for ghost %s", model_id, exc_info=True
            )

    async def _emit_model_unloaded(self, model_id: str) -> None:
        """Emit MODEL_UNLOADED so WebSocket forwarder notifies Stargate."""
        if self._event_bus is None:
            return
        try:
            from ...events.types import ModelUnloaded

            await self._event_bus.publish_nowait(ModelUnloaded(model_id=model_id))
            logger.info("📡 Emitted MODEL_UNLOADED for ghost model %s", model_id)
        except Exception:
            logger.error(
                "Failed to emit MODEL_UNLOADED for %s", model_id, exc_info=True
            )

    async def _emit_ghost_cleaned(
        self, model_id: str, success: bool, vram_freed_mb: int | None
    ) -> None:
        if self._event_bus is None:
            return
        try:
            from ...events.types import GhostModelCleaned

            await self._event_bus.publish_nowait(
                GhostModelCleaned(
                    model_id=model_id,
                    success=success,
                    vram_freed_mb=vram_freed_mb,
                )
            )
        except Exception:
            logger.error(
                "Failed to publish ghost cleanup event for %s",
                model_id,
                exc_info=True,
            )
