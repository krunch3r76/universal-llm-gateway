"""GET /api/v1/status/resources - Resource status endpoint"""

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry
from src.core.queue_errors import handle_generic_error
from src.core.resources import resource_tracker
from src.routers.dependencies import get_model_registry, get_worker_controller
from src.schemas.resource_management import ResourceStatusResponse

router = APIRouter(prefix="/v1/status", tags=["Resource Management"])
logger = get_logger(__name__)


@router.get("/resources", response_model=ResourceStatusResponse)
async def get_resource_status(
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
):
    """
    Get current resource usage and model status on the gateway.

    Returns comprehensive information about:
    - Total and available VRAM/RAM
    - Currently loaded models
    - Models currently processing inference
    - Detailed resource usage per model
    """
    try:
        # Get system resource information
        system_resources = await resource_tracker.get_system_resources()

        # Get all available models from the registry
        models_data = model_registry.model_loaders_config.get("models", {})
        all_model_ids = []

        # Collect all model IDs (including aliases) - show all models regardless of enabled status
        for model_id, model_data in models_data.items():
            if isinstance(model_data, dict):
                all_model_ids.append(model_id)
            elif isinstance(model_data, str):  # Alias
                all_model_ids.append(model_id)

        # Get resource tracking information for models that are tracked
        tracked_models_info = resource_tracker.get_all_models_info()

        # Get loaded models from worker controller (resource tracker view)
        loaded_models = []
        try:
            active_models = (
                worker_controller.get_active_models()
            )  # Returns list, not coroutine
            if active_models:
                loaded_models = active_models
                # logger.info(f"🔍 Detected loaded models via worker controller: {active_models}")
        except Exception as e:
            logger.warning(f"Failed to get active models from worker controller: {e}")

        # Also check actual running processes (may catch orphaned workers)
        actual_processes = {}
        try:
            actual_processes = worker_controller.get_all_process_info()
            # If we have processes that aren't in loaded_models, log a warning
            process_model_ids = set(actual_processes.keys())
            tracker_model_ids = set(loaded_models)
            orphaned_processes = process_model_ids - tracker_model_ids
            missing_processes = tracker_model_ids - process_model_ids

            if orphaned_processes:
                logger.warning(
                    f"⚠️ Orphaned processes detected: {orphaned_processes}. "
                    f"This indicates cleanup_failed_worker didn't complete. "
                    f"Check load flow for issues."
                )
                # Don't reconcile - orphans should be fixed at source
                # Include in debug_info for visibility
            if missing_processes:
                logger.warning(
                    f"Resource tracker shows {len(missing_processes)} model(s) loaded but no processes found: {missing_processes}"
                )
        except Exception as e:
            logger.debug(f"Failed to get actual process info: {e}")

        # Get busy models from resource tracker
        busy_models = resource_tracker.get_busy_models()
        model_details = {}

        # Build model details for all loaded models
        for model_id in loaded_models:
            # logger.info(f"🔍 Building details for loaded model: {model_id}")

            tracked_info = tracked_models_info.get(model_id)
            if tracked_info:
                # Get process info for real-time memory measurement
                process_info = actual_processes.get(model_id, {})
                process_pid = process_info.get("pid")

                # Query actual VRAM/RAM usage from GPU/system at runtime
                # This captures current state including KV cache growth, context accumulation, etc.
                actual_vram_mb = tracked_info.vram_usage_mb  # Fallback to tracked
                actual_ram_mb = tracked_info.ram_usage_mb  # Fallback to tracked

                if process_pid:
                    # Get current resources via shared helper (logs growth if >200MB)
                    runtime_vram, runtime_ram = (
                        resource_tracker.get_current_process_resources(
                            pid=process_pid,
                            model_id=model_id,
                            baseline_vram_mb=tracked_info.vram_usage_mb,
                            baseline_ram_mb=tracked_info.ram_usage_mb,
                            log_growth_threshold_mb=200,
                        )
                    )

                    # Use measured values if available, fallback to tracked
                    if runtime_vram is not None:
                        actual_vram_mb = runtime_vram
                    if runtime_ram is not None:
                        actual_ram_mb = runtime_ram

                # Use real-time measured values (or tracked fallback)
                model_details[model_id] = {
                    "status": tracked_info.status.value,
                    "current_inference_start": tracked_info.current_inference_start,
                    "last_inference_end": tracked_info.last_inference_end,
                    "load_time": tracked_info.load_time,
                    "last_inference_time": tracked_info.last_inference_time,
                    "ram_usage": actual_ram_mb,
                    "vram_usage": actual_vram_mb,
                }
                # logger.info(f"🔍 Using tracked info for {model_id}: status={tracked_info.status.value}")

        # Enhanced debug info for troubleshooting
        debug_info = {
            "tracked_models_count": len(model_details),
            "loaded_models_count": len(loaded_models),
            "busy_models_count": len(busy_models),
            "actual_processes_count": len(actual_processes),
            "detection_method": "worker_controller",
            "validation_status": (
                "valid"
                if system_resources.available_vram_mb <= system_resources.total_vram_mb
                else "invalid"
            ),
            "all_model_ids_count": len(all_model_ids),
            "tracked_models_info_count": len(tracked_models_info),
            "process_model_ids": list(actual_processes.keys()),
            "tracker_model_ids": loaded_models,
        }

        return ResourceStatusResponse(
            total_vram_mb=system_resources.total_vram_mb,
            available_vram_mb=system_resources.available_vram_mb,
            total_ram_mb=system_resources.total_ram_mb,
            available_ram_mb=system_resources.available_ram_mb,
            loaded_models=loaded_models,
            busy_models=busy_models,
            model_details=model_details,
            debug_info=debug_info,
        )

    except Exception as e:
        logger.error(f"Error getting resource status: {e}")
        raise handle_generic_error(e, "get_resource_status")
