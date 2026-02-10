"""System metrics endpoint - /metrics"""

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_registry, get_worker_controller
from src.schemas.responses import MetricsResponse
from src.utils.monitoring import system_monitor

router = APIRouter()
logger = get_logger(__name__)


@router.get("/metrics", response_model=MetricsResponse, tags=["Health"])
async def get_metrics(
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
):
    """
    System metrics endpoint

    Returns comprehensive system metrics including CPU, memory, GPU, and model status.
    """
    # Collect system metrics
    system_info = system_monitor.get_system_info()
    cpu_info = system_monitor.get_cpu_info()
    memory_info = system_monitor.get_memory_info()
    system_monitor.get_disk_info()
    gpu_info = system_monitor.get_gpu_info()
    process_info = system_monitor.get_process_info()

    # Get model statistics
    model_stats = {"loaded_count": 0, "enabled_count": 0, "total_count": 0}

    if model_registry:
        model_counts = model_registry.get_model_count()
        # Map the keys from get_model_count() to the expected format
        model_stats["total_count"] = model_counts.get("total", 0)
        model_stats["enabled_count"] = model_counts.get("enabled", 0)
        # Get loaded count from the registry
        model_stats["loaded_count"] = len(model_registry.loaded_models)

    if worker_controller:
        manager_status = await worker_controller.get_status()
        model_stats["current_vram_usage_mb"] = manager_status.get(
            "current_vram_usage_mb", 0
        )
        model_stats["vram_usage_percent"] = manager_status.get("vram_usage_percent", 0)
        model_stats["max_vram_mb"] = manager_status.get("max_vram_usage_mb", 0)

    return MetricsResponse(
        system={
            "platform": system_info.get("platform", "unknown"),
            "platform_release": system_info.get("platform_release", "unknown"),
            "architecture": system_info.get("architecture", "unknown"),
            "hostname": system_info.get("hostname", "unknown"),
            "python_version": system_info.get("python_version", "unknown"),
            "cpu_count": cpu_info.get("count", 0),
            "cpu_usage_percent": cpu_info.get("usage_percent", 0),
            "cpu_frequency_mhz": cpu_info.get("frequency_mhz"),
            "load_average": cpu_info.get("load_average"),
            "uptime_seconds": system_monitor.get_uptime_seconds(),
        },
        memory={
            "total_mb": memory_info.get("total_mb", 0),
            "available_mb": memory_info.get("available_mb", 0),
            "used_mb": memory_info.get("used_mb", 0),
            "usage_percent": memory_info.get("usage_percent", 0),
            "swap_total_mb": memory_info.get("swap_total_mb", 0),
            "swap_used_mb": memory_info.get("swap_used_mb", 0),
            "swap_usage_percent": memory_info.get("swap_usage_percent", 0),
            "process_memory_mb": process_info.get("memory_mb", 0),
            "process_memory_percent": process_info.get("memory_percent", 0),
        },
        gpu=gpu_info,
        models=model_stats,
    )
