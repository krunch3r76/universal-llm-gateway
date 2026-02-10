"""
Resource Monitoring - Non-blocking resource tracking during inference.

Provides AsyncResourceMonitor for tracking VRAM/RAM usage during inference with:
- Non-blocking periodic monitoring
- Worker configuration association
- Real-time resource snapshots
- Database storage with async operations
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

# Initialize logger first
logger = get_logger(__name__)

try:
    import psutil
except ImportError:
    psutil = None
    logger.warning("psutil not available - RAM monitoring will be limited")

try:
    import pynvml

    pynvml.nvmlInit()
except ImportError:
    pynvml = None
    logger.warning("pynvml not available - GPU monitoring will be limited")


@dataclass
class InferenceResourceSnapshot:
    """Resource snapshot during inference"""

    model_id: str
    request_id: str
    worker_config: dict[str, Any]
    worker_pid: int | None = None
    timestamp: float = field(default_factory=time.time)
    # Worker-specific resources
    worker_vram_used_mb: int = 0
    worker_ram_used_mb: int = 0
    worker_vram_max_mb: int = 0
    worker_ram_max_mb: int = 0
    # Global system resources (for context)
    system_vram_used_mb: int = 0
    system_ram_used_mb: int = 0
    system_vram_max_mb: int = 0
    system_ram_max_mb: int = 0
    gpu_utilization: float = 0.0
    inference_duration: float = 0.0


class AsyncResourceMonitor:
    """Non-blocking resource monitoring during inference"""

    def __init__(self, event_store=None):
        """
        Initialize resource monitor.

        Args:
            event_store: AsyncEventStore for persisting snapshots
        """
        self.event_store = event_store
        self.monitoring_tasks: dict[str, asyncio.Task] = {}
        self.snapshot_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ResourceMonitor"
        )

        # Start background storage task
        self.storage_task = asyncio.create_task(self._background_storage())

        logger.info("📊 AsyncResourceMonitor initialized")

    async def start_monitoring(
        self,
        model_id: str,
        request_id: str,
        worker_config: dict[str, Any],
        worker_pid: int | None = None,
        monitoring_interval: float = 1.0,
    ):
        """
        Start non-blocking resource monitoring.

        Args:
            model_id: Model identifier
            request_id: Request identifier
            worker_config: Worker configuration to associate with readings
            worker_pid: Worker process PID for process-specific monitoring
            monitoring_interval: Monitoring interval in seconds
        """
        if model_id in self.monitoring_tasks:
            logger.warning(f"Monitoring already active for {model_id}")
            return

        # Start monitoring task
        self.monitoring_tasks[model_id] = asyncio.create_task(
            self._monitor_resources(
                model_id, request_id, worker_config, worker_pid, monitoring_interval
            )
        )

        logger.info(
            f"📊 Started resource monitoring for {model_id} "
            f"(request: {request_id}, PID: {worker_pid})"
        )

    async def stop_monitoring(self, model_id: str):
        """Stop resource monitoring"""
        if model_id in self.monitoring_tasks:
            self.monitoring_tasks[model_id].cancel()
            del self.monitoring_tasks[model_id]
            logger.info(f"📊 Stopped resource monitoring for {model_id}")

    async def _monitor_resources(
        self,
        model_id: str,
        request_id: str,
        worker_config: dict[str, Any],
        worker_pid: int | None,
        monitoring_interval: float,
    ):
        """
        Monitor resources during inference.

        ⚠️ POLLING JUSTIFIED: Necessary hardware monitoring, bounded duration.

        This is NOT an anti-pattern because:
        1. Duration bounded to inference time (typically seconds to minutes)
        2. No event-based alternative for GPU/RAM metrics
        3. NVML events exist but are complex and less reliable
        4. Interval is configurable and can be tuned per-deployment

        DECISION: Keep polling. This is the standard approach for hardware monitoring.
        """
        start_time = time.time()
        max_worker_vram = 0
        max_worker_ram = 0
        max_system_vram = 0
        max_system_ram = 0

        try:
            while True:
                # Get worker-specific usage (if PID available)
                worker_vram, worker_ram = await self._get_worker_resources_fast(
                    worker_pid
                )

                # Get system-wide usage for context
                system_vram = await self._get_current_vram_fast()
                system_ram = await self._get_current_ram_fast()

                # Track maximums
                max_worker_vram = max(max_worker_vram, worker_vram)
                max_worker_ram = max(max_worker_ram, worker_ram)
                max_system_vram = max(max_system_vram, system_vram)
                max_system_ram = max(max_system_ram, system_ram)

                # Create snapshot
                snapshot = InferenceResourceSnapshot(
                    model_id=model_id,
                    request_id=request_id,
                    worker_config=worker_config,
                    worker_pid=worker_pid,
                    timestamp=time.time(),
                    # Worker-specific resources
                    worker_vram_used_mb=worker_vram,
                    worker_ram_used_mb=worker_ram,
                    worker_vram_max_mb=max_worker_vram,
                    worker_ram_max_mb=max_worker_ram,
                    # System-wide resources (for context)
                    system_vram_used_mb=system_vram,
                    system_ram_used_mb=system_ram,
                    system_vram_max_mb=max_system_vram,
                    system_ram_max_mb=max_system_ram,
                    gpu_utilization=await self._get_gpu_utilization_fast(),
                    inference_duration=time.time() - start_time,
                )

                # Queue for background storage (non-blocking)
                try:
                    self.snapshot_queue.put_nowait(snapshot)
                except asyncio.QueueFull:
                    logger.warning("Snapshot queue full, dropping snapshot")

                # Non-blocking sleep
                await asyncio.sleep(monitoring_interval)

        except asyncio.CancelledError:
            # Store final snapshot
            final_snapshot = InferenceResourceSnapshot(
                model_id=model_id,
                request_id=request_id,
                worker_config=worker_config,
                worker_pid=worker_pid,
                timestamp=time.time(),
                # Worker-specific resources
                worker_vram_used_mb=worker_vram,
                worker_ram_used_mb=worker_ram,
                worker_vram_max_mb=max_worker_vram,
                worker_ram_max_mb=max_worker_ram,
                # System-wide resources (for context)
                system_vram_used_mb=system_vram,
                system_ram_used_mb=system_ram,
                system_vram_max_mb=max_system_vram,
                system_ram_max_mb=max_system_ram,
                gpu_utilization=await self._get_gpu_utilization_fast(),
                inference_duration=time.time() - start_time,
            )

            try:
                self.snapshot_queue.put_nowait(final_snapshot)
            except asyncio.QueueFull:
                logger.warning("Final snapshot queue full")

    async def _background_storage(self):
        """Background task to store snapshots without blocking"""
        while True:
            try:
                # Wait for snapshots (non-blocking)
                snapshot = await self.snapshot_queue.get()

                # Store in database (non-blocking)
                if self.event_store:
                    await self._store_snapshot_async(snapshot)

                # Mark task as done
                self.snapshot_queue.task_done()

            except Exception as e:
                logger.error(f"Background storage error: {e}")
                await asyncio.sleep(1)  # Brief pause on error

    async def _store_snapshot_async(self, snapshot: InferenceResourceSnapshot):
        """Store resource snapshot asynchronously"""
        try:
            # Create event for storage
            from .types import InferenceResourceUpdate

            event = InferenceResourceUpdate(
                model_id=snapshot.model_id,
                request_id=snapshot.request_id,
                timestamp=snapshot.timestamp,
                vram_used_mb=snapshot.worker_vram_used_mb,
                ram_used_mb=snapshot.worker_ram_used_mb,
                vram_max_mb=snapshot.worker_vram_max_mb,
                ram_max_mb=snapshot.worker_ram_max_mb,
                gpu_utilization=snapshot.gpu_utilization,
                inference_duration=snapshot.inference_duration,
                worker_config=snapshot.worker_config,
            )

            # Store event
            await self.event_store.store_event(event)

        except Exception as e:
            logger.error(f"Failed to store resource snapshot: {e}")

    async def _get_worker_resources_fast(
        self, worker_pid: int | None
    ) -> tuple[int, int]:
        """Fast worker-specific resource check (minimal overhead)"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._get_worker_resources_sync, worker_pid
            )
        except Exception:
            return 0, 0

    def _get_worker_resources_sync(self, worker_pid: int | None) -> tuple[int, int]:
        """Synchronous worker resource check (runs in thread pool)"""
        worker_vram = 0
        worker_ram = 0

        if not worker_pid:
            return worker_vram, worker_ram

        try:
            # Get worker process
            process = psutil.Process(worker_pid) if psutil else None
            if not process:
                return worker_vram, worker_ram

            # Get worker RAM usage
            worker_ram = int(process.memory_info().rss / (1024 * 1024))  # RSS in MB

            # Get worker VRAM usage (if process has GPU access)
            if pynvml:
                try:
                    # Find which GPU the process is using
                    for device_id in range(pynvml.nvmlDeviceGetCount()):
                        handle = pynvml.nvmlDeviceGetHandleByIndex(device_id)
                        # Get processes using this GPU
                        procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
                        for proc in procs:
                            if proc.pid == worker_pid:
                                worker_vram = int(
                                    proc.usedGpuMemory / (1024 * 1024)
                                )  # Convert to MB
                                break
                        if worker_vram > 0:
                            break
                except Exception:
                    # Fallback: estimate VRAM usage based on process memory
                    # This is a rough estimate - actual GPU memory tracking is complex
                    pass

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Process no longer exists or access denied
            pass
        except Exception:
            # Other errors - continue with 0 values
            pass

        return worker_vram, worker_ram

    async def _get_current_vram_fast(self) -> int:
        """Fast VRAM check (minimal overhead)"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._get_current_vram_sync
            )
        except Exception:
            return 0

    def _get_current_vram_sync(self) -> int:
        """Synchronous VRAM check (runs in thread pool)"""
        try:
            if pynvml:
                device_count = pynvml.nvmlDeviceGetCount()
                total_used = 0
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    total_used += int(mem_info.used / (1024 * 1024))
                return total_used
        except Exception:
            pass
        return 0

    async def _get_current_ram_fast(self) -> int:
        """Fast RAM check (minimal overhead)"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.executor, self._get_current_ram_sync)
        except Exception:
            return 0

    def _get_current_ram_sync(self) -> int:
        """Synchronous RAM check (runs in thread pool)"""
        try:
            if psutil:
                return int(psutil.virtual_memory().used / (1024 * 1024))
        except Exception:
            pass
        return 0

    async def _get_gpu_utilization_fast(self) -> float:
        """Fast GPU utilization check"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._get_gpu_utilization_sync
            )
        except Exception:
            return 0.0

    def _get_gpu_utilization_sync(self) -> float:
        """Synchronous GPU utilization check (runs in thread pool)"""
        try:
            if pynvml:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                return util.gpu
        except Exception:
            pass
        return 0.0

    async def get_model_snapshots(
        self, model_id: str, limit: int = 100, since: float | None = None
    ) -> list[dict]:
        """Get resource snapshots for a model (non-blocking)"""
        try:
            if not self.event_store:
                return []

            # Query events for this model
            events = await self.event_store.query_events(
                event_type="INFERENCE_RESOURCE_UPDATE", since=since, limit=limit
            )

            # Filter by model_id and convert to snapshots
            snapshots = []
            for event in events:
                payload = event.get("event_data", {}).get("payload", {})
                if payload.get("model_id") == model_id:
                    snapshots.append(
                        {
                            "timestamp": payload.get("timestamp"),
                            "vram_used_mb": payload.get("vram_used_mb"),
                            "ram_used_mb": payload.get("ram_used_mb"),
                            "vram_max_mb": payload.get("vram_max_mb"),
                            "ram_max_mb": payload.get("ram_max_mb"),
                            "gpu_utilization": payload.get("gpu_utilization"),
                            "inference_duration": payload.get("inference_duration"),
                            "worker_config": payload.get("worker_config"),
                        }
                    )

            return snapshots

        except Exception as e:
            logger.error(f"Failed to get model snapshots: {e}")
            return []

    async def close(self):
        """Close resource monitor"""
        # Stop all monitoring tasks
        for task in self.monitoring_tasks.values():
            task.cancel()

        # Stop storage task
        if hasattr(self, "storage_task"):
            self.storage_task.cancel()

        # Shutdown executor
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)

        logger.info("📊 AsyncResourceMonitor closed")
