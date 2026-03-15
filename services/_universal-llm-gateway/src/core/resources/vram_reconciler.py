"""Periodic VRAM reconciliation for phantom-model and ghost-model detection.

Phantom model: worker process running but not tracked (orphan).
Ghost model: tracked as loaded but engine subprocess dead (stale state).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any, Final, Protocol

from universal_logging import get_logger

from .hardware import get_vram_info
from .types import ModelResourceInfo


class _ResourceTrackerProto(Protocol):
    _models: dict[str, ModelResourceInfo]

    def get_loaded_models(self) -> list[str]: ...

    async def get_system_resources(self) -> dict[str, Any]: ...


class _UnloadResultProto(Protocol):
    success: bool
    reason: str | None


class _WorkerControllerProto(Protocol):
    def get_running_worker_processes(self) -> dict[str, int]: ...

    def get_engine_pid(self, model_id: str) -> int | None: ...

    async def check_engine_health(self, model_id: str) -> bool: ...

    async def unload_model(
        self, model_id: str, force: bool = False
    ) -> _UnloadResultProto: ...


class _EventBusProto(Protocol):
    async def publish_async_nowait(self, event: object) -> None: ...


logger = get_logger(__name__)

RECONCILE_INTERVAL_S: Final[float] = 60.0
VRAM_DISCREPANCY_THRESHOLD_MB: Final[int] = 2000


class VramReconciler:
    """Detect and clean up phantom and ghost GPU workers.

    Runs every RECONCILE_INTERVAL_S and performs three sweeps:
    1. Phantom sweep: running process ∉ tracked → force-unload orphan.
    2. Ghost sweep: tracked model whose engine is dead → unload + emit MODEL_UNLOADED.
    3. VRAM discrepancy: |hardware − catalog| > threshold → alert.
    """

    def __init__(
        self,
        resource_tracker: _ResourceTrackerProto,
        worker_controller: _WorkerControllerProto,
        event_bus: _EventBusProto | None = None,
    ) -> None:
        self._resource_tracker: _ResourceTrackerProto = resource_tracker
        self._worker_controller: _WorkerControllerProto = worker_controller
        self._event_bus: _EventBusProto | None = event_bus
        self._task: asyncio.Task[None] | None = None

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

    # ------------------------------------------------------------------
    # Core reconciliation
    # ------------------------------------------------------------------

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

        # Sweep 1: orphaned processes (running but not tracked)
        phantom_models = sorted(set(running_processes) - tracked_models)
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

        # Sweep 2: ghost models (tracked + worker alive, but engine dead)
        await self._sweep_ghost_models(tracked_models, running_processes)

        # Sweep 3: VRAM discrepancy (bidirectional)
        remaining_tracked = set(self._resource_tracker.get_loaded_models())
        await self._check_vram_discrepancy(remaining_tracked)

    # ------------------------------------------------------------------
    # Ghost model sweep (tracked but engine dead)
    # ------------------------------------------------------------------

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

        # Update resource tracker regardless — the model is definitely not loaded
        self._clear_ghost_from_tracker(model_id)

        # Emit MODEL_UNLOADED so Stargate learns the model is gone
        await self._emit_model_unloaded(model_id)

        # Trigger SYSTEM_RESOURCES_UPDATED with corrected VRAM accounting
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
        """Mark ghost model as NOT_LOADED in resource tracker."""
        try:
            from .types import ModelStatus

            info = self._resource_tracker._models.get(model_id)
            if info is not None:
                info.status = ModelStatus.NOT_LOADED
                info.vram_usage_mb = 0
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
            from ..events.types import ModelUnloaded

            await self._event_bus.publish_async_nowait(ModelUnloaded(model_id=model_id))
            logger.info("📡 Emitted MODEL_UNLOADED for ghost model %s", model_id)
        except Exception:
            logger.error(
                "Failed to emit MODEL_UNLOADED for %s", model_id, exc_info=True
            )

    def _get_tracked_vram(self, model_id: str) -> int:
        info = self._resource_tracker._models.get(model_id)
        return info.vram_usage_mb if info else 0

    # ------------------------------------------------------------------
    # Helpers shared by phantom and ghost paths
    # ------------------------------------------------------------------

    @staticmethod
    def _is_engine_pid_alive(pid: int) -> bool:
        """Check engine subprocess liveness via signal 0 (no RPC needed)."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            logger.error(
                "Permission denied when checking PID %d liveness. Assuming dead or unmanageable.",
                pid,
            )
            return False
        except OSError:
            return False

    def _get_tracker_status(self, model_id: str) -> str | None:
        info = self._resource_tracker._models.get(model_id)
        if info and info.status:
            return info.status.value
        return None

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
            from ..events.types import PhantomModelDetected

            await self._event_bus.publish_async_nowait(
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

    # ------------------------------------------------------------------
    # VRAM discrepancy (bidirectional)
    # ------------------------------------------------------------------

    async def _check_vram_discrepancy(self, tracked_models: set[str]) -> None:
        hw = get_vram_info()
        hardware_used = hw["total_vram_mb"] - hw["available_vram_mb"]

        catalog_used = 0
        for model_id in tracked_models:
            info = self._resource_tracker._models.get(model_id)
            if info is not None:
                catalog_used += info.vram_usage_mb

        discrepancy = hardware_used - catalog_used

        # Positive discrepancy: hardware uses more than catalog tracks (unmanaged GPU procs)
        if discrepancy > VRAM_DISCREPANCY_THRESHOLD_MB:
            logger.warning(
                "VRAM over-use detected: hardware=%sMB catalog=%sMB delta=+%sMB "
                "(unmanaged GPU processes likely)",
                hardware_used,
                catalog_used,
                discrepancy,
            )
            await self._emit_vram_discrepancy(
                hardware_used=hardware_used,
                catalog_used=catalog_used,
                discrepancy=discrepancy,
                tracked_models=sorted(tracked_models),
            )
            unmanaged = await self._scan_gpu_processes()
            if unmanaged:
                logger.error("Unmanaged GPU processes detected: %s", unmanaged)

        # Negative discrepancy: catalog claims more than hardware uses (ghost VRAM)
        # Only alert if substantial — ghost sweep handles the actual cleanup
        elif discrepancy < -VRAM_DISCREPANCY_THRESHOLD_MB:
            logger.warning(
                "VRAM ghost detected: catalog=%sMB but hardware only=%sMB "
                "(delta=%sMB — tracked models not on GPU)",
                catalog_used,
                hardware_used,
                discrepancy,
            )
            await self._emit_vram_discrepancy(
                hardware_used=hardware_used,
                catalog_used=catalog_used,
                discrepancy=discrepancy,
                tracked_models=sorted(tracked_models),
            )

    async def _emit_vram_discrepancy(
        self,
        hardware_used: int,
        catalog_used: int,
        discrepancy: int,
        tracked_models: list[str],
    ) -> None:
        if self._event_bus is None:
            logger.warning(
                "event_bus unavailable - VRAM discrepancy event not published (delta=%sMB)",
                discrepancy,
            )
            return
        try:
            from ..events.types import VramPhantomDetected

            await self._event_bus.publish_async_nowait(
                VramPhantomDetected(
                    hardware_used_mb=hardware_used,
                    catalog_used_mb=catalog_used,
                    discrepancy_mb=discrepancy,
                    tracked_models=tracked_models,
                )
            )
        except Exception:
            logger.error("Failed to publish VRAM discrepancy event", exc_info=True)

    # ------------------------------------------------------------------
    # Force cleanup (phantom models — orphaned processes)
    # ------------------------------------------------------------------

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
            from ..events.types import PhantomModelCleaned

            await self._event_bus.publish_async_nowait(
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

    async def _emit_ghost_cleaned(
        self, model_id: str, success: bool, vram_freed_mb: int | None
    ) -> None:
        if self._event_bus is None:
            return
        try:
            from ..events.types import GhostModelCleaned

            await self._event_bus.publish_async_nowait(
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

    async def _kill_worker_process(self, model_id: str, pid: int) -> bool:
        """Last-resort kill for known Gateway-managed worker PID."""
        try:
            os.kill(pid, 9)
            logger.warning(
                "Force-killed managed worker process for phantom model %s (pid=%s)",
                model_id,
                pid,
            )
            return True
        except ProcessLookupError:
            logger.warning(
                "Phantom worker pid already exited (model=%s pid=%s)", model_id, pid
            )
            return True
        except Exception:
            logger.error(
                "Failed to kill phantom worker process (model=%s pid=%s)",
                model_id,
                pid,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # GPU process scanning
    # ------------------------------------------------------------------

    def _get_known_pids(self) -> set[int]:
        """Snapshot known worker AND engine subprocess PIDs."""
        pids = set(self._worker_controller.get_running_worker_processes().values())
        for model_id in self._resource_tracker.get_loaded_models():
            engine_pid = self._worker_controller.get_engine_pid(model_id)
            if engine_pid is not None:
                pids.add(engine_pid)
        return pids

    async def _scan_gpu_processes(self) -> list[dict[str, int]]:
        known_pids = self._get_known_pids()
        known_pids.add(os.getpid())

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._scan_gpu_processes_sync, known_pids),
                timeout=5.0,
            )
            return result
        except TimeoutError:
            logger.error("GPU process scan timed out after 5s")
            return []
        except Exception:
            logger.warning("GPU process scan failed", exc_info=True)
            return []

    def _scan_gpu_processes_sync(self, known_pids: set[int]) -> list[dict[str, int]]:
        try:
            import pynvml  # type: ignore
        except ImportError:
            logger.debug("pynvml not available; GPU process scan skipped")
            return []

        unmanaged: list[dict[str, int]] = []
        initialized = False
        try:
            pynvml.nvmlInit()
            initialized = True
            for gpu_idx in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_idx)
                processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                for proc in processes:
                    if proc.pid in known_pids:
                        continue
                    vram_mb = int((proc.usedGpuMemory or 0) / (1024 * 1024))
                    unmanaged.append({"pid": int(proc.pid), "vram_mb": vram_mb})
        except Exception:
            logger.warning("Synchronous GPU process scan failed", exc_info=True)
            return []
        finally:
            if initialized:
                with contextlib.suppress(Exception):
                    pynvml.nvmlShutdown()

        return unmanaged
