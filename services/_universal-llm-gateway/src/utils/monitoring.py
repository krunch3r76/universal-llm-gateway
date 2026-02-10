"""System monitoring and metrics utilities"""

import platform
import time
from dataclasses import dataclass
from typing import Any

import psutil
from universal_logging import get_logger


@dataclass
class SystemMetrics:
    """System metrics container"""

    timestamp: float
    cpu_usage_percent: float
    memory_total_mb: float
    memory_available_mb: float
    memory_usage_percent: float
    disk_usage_percent: float
    gpu_info: dict[str, Any] | None = None


class SystemMonitor:
    """System monitoring and health checking"""

    def __init__(self):
        self.start_time = time.time()
        self.logger = get_logger(__name__)

    def get_uptime_seconds(self) -> float:
        """Get server uptime in seconds"""
        return time.time() - self.start_time

    def get_cpu_info(self) -> dict[str, Any]:
        """Get CPU information"""
        try:
            return {
                "count": psutil.cpu_count(),
                "usage_percent": psutil.cpu_percent(
                    interval=0
                ),  # Non-blocking, returns immediately
                "load_average": psutil.getloadavg()
                if hasattr(psutil, "getloadavg")
                else None,
                "frequency_mhz": psutil.cpu_freq().current
                if psutil.cpu_freq()
                else None,
            }
        except Exception as e:
            self.logger.error(f"Error getting CPU info: {e}")
            return {"error": str(e)}

    def get_memory_info(self) -> dict[str, Any]:
        """Get memory information"""
        try:
            virtual = psutil.virtual_memory()
            swap = psutil.swap_memory()

            return {
                "total_mb": round(virtual.total / (1024 * 1024), 2),
                "available_mb": round(virtual.available / (1024 * 1024), 2),
                "used_mb": round(virtual.used / (1024 * 1024), 2),
                "usage_percent": virtual.percent,
                "swap_total_mb": round(swap.total / (1024 * 1024), 2),
                "swap_used_mb": round(swap.used / (1024 * 1024), 2),
                "swap_usage_percent": swap.percent,
            }
        except Exception as e:
            self.logger.error(f"Error getting memory info: {e}")
            return {"error": str(e)}

    def get_disk_info(self) -> dict[str, Any]:
        """Get disk usage information"""
        try:
            disk = psutil.disk_usage("/")
            return {
                "total_gb": round(disk.total / (1024 * 1024 * 1024), 2),
                "used_gb": round(disk.used / (1024 * 1024 * 1024), 2),
                "free_gb": round(disk.free / (1024 * 1024 * 1024), 2),
                "usage_percent": round((disk.used / disk.total) * 100, 2),
            }
        except Exception as e:
            self.logger.error(f"Error getting disk info: {e}")
            return {"error": str(e)}

    def get_gpu_info(self) -> dict[str, Any] | None:
        """Get GPU information (if available)"""
        try:
            # Try to get NVIDIA GPU info
            import pynvml

            pynvml.nvmlInit()

            device_count = pynvml.nvmlDeviceGetCount()
            gpus = []

            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name_raw = pynvml.nvmlDeviceGetName(handle)
                # Handle both string and bytes return values
                name = (
                    name_raw.decode("utf-8")
                    if isinstance(name_raw, bytes)
                    else name_raw
                )

                # Memory info
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_mb = mem_info.total // (1024 * 1024)
                used_mb = mem_info.used // (1024 * 1024)
                free_mb = mem_info.free // (1024 * 1024)

                # Utilization
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)

                # Temperature
                try:
                    temp = pynvml.nvmlDeviceGetTemperature(
                        handle, pynvml.NVML_TEMPERATURE_GPU
                    )
                except:
                    temp = None

                gpus.append(
                    {
                        "id": i,
                        "name": name,
                        "total_vram_mb": total_mb,
                        "used_vram_mb": used_mb,
                        "free_vram_mb": free_mb,
                        "vram_usage_percent": round((used_mb / total_mb) * 100, 2),
                        "gpu_utilization_percent": util.gpu,
                        "memory_utilization_percent": util.memory,
                        "temperature_c": temp,
                    }
                )

            # Handle both string and bytes return values for driver version
            driver_version_raw = pynvml.nvmlSystemGetDriverVersion()
            driver_version = (
                driver_version_raw.decode("utf-8")
                if isinstance(driver_version_raw, bytes)
                else driver_version_raw
            )

            return {
                "device_count": device_count,
                "devices": gpus,
                "driver_version": driver_version,
            }

        except ImportError:
            # pynvml not available
            return None
        except Exception as e:
            self.logger.warning(f"Could not get GPU info: {e}")
            return None

    def get_system_info(self) -> dict[str, Any]:
        """Get basic system information"""
        try:
            return {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "hostname": platform.node(),
            }
        except Exception as e:
            self.logger.error(f"Error getting system info: {e}")
            return {"error": str(e)}

    def get_process_info(self) -> dict[str, Any]:
        """Get current process information"""
        try:
            process = psutil.Process()

            return {
                "pid": process.pid,
                "memory_mb": round(process.memory_info().rss / (1024 * 1024), 2),
                "memory_percent": round(process.memory_percent(), 2),
                "cpu_percent": round(process.cpu_percent(), 2),
                "num_threads": process.num_threads(),
                "create_time": process.create_time(),
                "status": process.status(),
            }
        except Exception as e:
            self.logger.error(f"Error getting process info: {e}")
            return {"error": str(e)}

    def collect_metrics(self) -> SystemMetrics:
        """Collect comprehensive system metrics"""
        timestamp = time.time()

        # Get basic metrics
        cpu_info = self.get_cpu_info()
        memory_info = self.get_memory_info()
        disk_info = self.get_disk_info()
        gpu_info = self.get_gpu_info()

        return SystemMetrics(
            timestamp=timestamp,
            cpu_usage_percent=cpu_info.get("usage_percent", 0),
            memory_total_mb=memory_info.get("total_mb", 0),
            memory_available_mb=memory_info.get("available_mb", 0),
            memory_usage_percent=memory_info.get("usage_percent", 0),
            disk_usage_percent=disk_info.get("usage_percent", 0),
            gpu_info=gpu_info,
        )

    def get_health_status(self) -> str:
        """Determine overall system health status"""
        try:
            metrics = self.collect_metrics()

            # Define thresholds
            cpu_threshold = 90.0
            memory_threshold = 90.0
            disk_threshold = 95.0

            issues = []

            if metrics.cpu_usage_percent > cpu_threshold:
                issues.append(f"High CPU usage: {metrics.cpu_usage_percent:.1f}%")

            if metrics.memory_usage_percent > memory_threshold:
                issues.append(f"High memory usage: {metrics.memory_usage_percent:.1f}%")

            if metrics.disk_usage_percent > disk_threshold:
                issues.append(f"High disk usage: {metrics.disk_usage_percent:.1f}%")

            # Check GPU if available
            if metrics.gpu_info and metrics.gpu_info.get("devices"):
                for gpu in metrics.gpu_info["devices"]:
                    vram_usage = gpu.get("vram_usage_percent", 0)
                    if vram_usage > 95:
                        issues.append(
                            f"High GPU {gpu['id']} VRAM usage: {vram_usage:.1f}%"
                        )

            if issues:
                return "degraded"  # System has issues but is functioning
            else:
                return "healthy"

        except Exception as e:
            self.logger.error(f"Error determining health status: {e}")
            return "unhealthy"


# Global monitor instance
system_monitor = SystemMonitor()
