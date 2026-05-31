"""
Simple resource monitor for single worker process.

Provides on-demand resource monitoring without background collection
or multi-worker complexity.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import psutil
from universal_logging import get_logger

from ..core.config import ResourceMonitoringConfig
from ..core.types import ProcessResourceUsage

# Try to import pynvml for GPU monitoring
try:
    import pynvml

    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    pynvml = None


class SimpleResourceMonitor:
    """
    Simple resource monitor for a single worker process.

    Provides on-demand resource monitoring with peak tracking.
    No background collection or multi-worker complexity.
    """

    def __init__(self, resource_config: ResourceMonitoringConfig | None = None):
        """
        Initialize the simple resource monitor.

        Args:
            resource_config: Resource monitoring configuration
        """
        self.config = resource_config or ResourceMonitoringConfig()
        self._logger = get_logger("process_ipc.services.simple_resource_monitor")
        self._structured_logger = get_logger(
            "process_ipc.services.simple_resource_monitor"
        )

        # Current worker
        self._worker_id: str | None = None
        self._worker_pid: int | None = None

        # Peak tracking
        self._peak_ram_bytes = 0
        self._peak_vram_bytes = 0
        self._peak_timestamp: datetime | None = None

        # GPU monitoring state
        self._gpu_available = False
        self._gpu_initialized = False

        # Thread pool for non-blocking collection
        self._executor: ThreadPoolExecutor | None = None

        # Initialize GPU monitoring if enabled
        if self.config.enable_gpu_monitoring and PYNVML_AVAILABLE:
            self._init_gpu_monitoring()

    def _init_gpu_monitoring(self) -> None:
        """Initialize GPU monitoring with pynvml."""
        try:
            pynvml.nvmlInit()
            self._gpu_initialized = True
            self._gpu_available = True
            device_count = pynvml.nvmlDeviceGetCount()
            self._logger.info(
                f"GPU monitoring initialized. Found {device_count} GPU(s)"
            )
        except Exception as e:
            self._logger.warning(f"Failed to initialize GPU monitoring: {e}")
            self._gpu_available = False

    def set_worker(self, worker_id: str, pid: int) -> None:
        """
        Set the worker to monitor.

        Args:
            worker_id: Worker identifier
            pid: Process ID
        """
        self._worker_id = worker_id
        self._worker_pid = pid
        self._reset_peaks()
        self._logger.debug(
            f"Set worker {worker_id} (PID {pid}) for resource monitoring"
        )

    def clear_worker(self) -> None:
        """Clear the current worker."""
        self._worker_id = None
        self._worker_pid = None
        self._reset_peaks()
        self._logger.debug("Cleared worker from resource monitoring")

    def _reset_peaks(self) -> None:
        """Reset peak tracking."""
        self._peak_ram_bytes = 0
        self._peak_vram_bytes = 0
        self._peak_timestamp = None

    async def get_current_usage(self) -> ProcessResourceUsage | None:
        """
        Get current resource usage for the worker.

        Returns:
            ProcessResourceUsage or None if no worker set or collection failed
        """
        if not self._worker_id or not self._worker_pid:
            self._logger.debug("No worker set for resource monitoring")
            return None

        if not self.config.enable_resource_monitoring:
            self._logger.debug("Resource monitoring disabled")
            return None

        try:
            # Create thread pool if needed
            if not self._executor:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="resource_monitor"
                )

            # Collect resources in thread pool (non-blocking)
            loop = asyncio.get_event_loop()
            usage = await loop.run_in_executor(
                self._executor,
                self._collect_resources_sync,
                self._worker_id,
                self._worker_pid,
            )

            if usage:
                # Update peaks
                self._update_peaks(usage)

                # Call callback if configured
                if self.config.on_resource_update:
                    try:
                        if asyncio.iscoroutinefunction(self.config.on_resource_update):
                            await self.config.on_resource_update(usage)
                        else:
                            self.config.on_resource_update(usage)
                    except Exception as e:
                        self._logger.error(f"Error in resource update callback: {e}")

            return usage

        except Exception as e:
            self._logger.error(f"Error collecting resources: {e}")
            return None

    def _collect_resources_sync(
        self, worker_id: str, pid: int
    ) -> ProcessResourceUsage | None:
        """
        Synchronously collect resource usage (runs in thread pool).

        Args:
            worker_id: Worker identifier
            pid: Process ID

        Returns:
            ProcessResourceUsage or None if collection failed
        """
        try:
            # Get process
            proc = psutil.Process(pid)

            # Get memory info
            mem_info = proc.memory_info()
            ram_used = mem_info.rss
            ram_percent = proc.memory_percent()

            # Get system memory info
            system_mem = psutil.virtual_memory()
            system_ram_total = system_mem.total
            system_ram_available = system_mem.available

            # Get CPU info
            cpu_percent = proc.cpu_percent(interval=0.1)
            num_threads = proc.num_threads()

            # Get GPU memory info if available
            vram_used = None
            vram_total = None
            vram_percent = None

            if self._gpu_available and self._gpu_initialized:
                vram_info = self._get_gpu_memory_for_process(pid)
                if vram_info:
                    vram_used, vram_total, vram_percent = vram_info

            return ProcessResourceUsage(
                process_id=worker_id,
                pid=pid,
                timestamp=datetime.now(),
                ram_used=ram_used,
                ram_percent=ram_percent,
                vram_used=vram_used,
                vram_total=vram_total,
                vram_percent=vram_percent,
                system_ram_total=system_ram_total,
                system_ram_available=system_ram_available,
                cpu_percent=cpu_percent,
                num_threads=num_threads,
            )

        except psutil.NoSuchProcess:
            self._logger.warning(f"Process {pid} no longer exists")
            return None
        except Exception as e:
            self._logger.error(f"Error collecting resources for PID {pid}: {e}")
            return None

    def _get_gpu_memory_for_process(self, pid: int) -> tuple | None:
        """
        Get GPU memory usage for a specific process.

        Args:
            pid: Process ID

        Returns:
            Tuple of (vram_used, vram_total, vram_percent) or None
        """
        if not PYNVML_AVAILABLE or not self._gpu_initialized:
            return None

        try:
            device_count = pynvml.nvmlDeviceGetCount()

            total_vram_used = 0
            total_vram_total = 0

            # Check all GPUs
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)

                # Get processes running on this GPU
                try:
                    processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)

                    # Find our process
                    for proc in processes:
                        if proc.pid == pid:
                            total_vram_used += proc.usedGpuMemory
                            total_vram_total += mem_info.total
                            break
                except Exception as e:
                    self._logger.debug(f"Error getting GPU processes: {e}")

            if total_vram_used > 0:
                vram_percent = (
                    (total_vram_used / total_vram_total * 100)
                    if total_vram_total > 0
                    else 0
                )
                return (total_vram_used, total_vram_total, vram_percent)

            return None

        except Exception as e:
            self._logger.debug(f"Error getting GPU memory for PID {pid}: {e}")
            return None

    def _update_peaks(self, usage: ProcessResourceUsage) -> None:
        """Update peak usage tracking."""
        # Update peak RAM
        if usage.ram_used > self._peak_ram_bytes:
            self._peak_ram_bytes = usage.ram_used
            self._peak_timestamp = usage.timestamp

        # Update peak VRAM
        if usage.vram_used and usage.vram_used > self._peak_vram_bytes:
            self._peak_vram_bytes = usage.vram_used

    def get_peak_usage(self) -> dict[str, Any]:
        """
        Get peak usage for the current worker.

        Returns:
            Dictionary with peak usage information
        """
        return {
            "worker_id": self._worker_id,
            "peak_ram_bytes": self._peak_ram_bytes,
            "peak_ram_gb": self._peak_ram_bytes / 1024**3,
            "peak_vram_bytes": self._peak_vram_bytes,
            "peak_vram_gb": self._peak_vram_bytes / 1024**3,
            "peak_timestamp": self._peak_timestamp,
        }

    def reset_peak_usage(self) -> None:
        """Reset peak usage tracking."""
        self._reset_peaks()
        self._logger.debug("Reset peak usage tracking")

    def shutdown(self) -> None:
        """Shutdown the resource monitor."""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

        if self._gpu_initialized and PYNVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
                self._gpu_initialized = False
            except Exception as e:
                self._logger.warning(f"Error shutting down GPU monitoring: {e}")

        self._logger.debug("Simple resource monitor shutdown")
