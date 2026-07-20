"""GPU process scanning and last-resort worker process termination."""

from __future__ import annotations

import asyncio
import contextlib
import os

from universal_logging import get_logger

logger = get_logger(__name__)


class GpuProcessMixin:
    """Scan NVML for unmanaged GPU consumers and force-kill known worker PIDs."""

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
