"""GET /api/v1/status/detailed - Comprehensive gateway status for proxy orchestration"""

import time

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from src.core.resources import resource_tracker
from src.routers.dependencies import (
    get_gateway_config,
    get_model_registry,
    get_worker_controller,
)
from src.schemas.gateway_status import (
    DetailedStatusResponse,
    GatewayHealthInfo,
    ModelResourceUsage,
    ModelStatusDetail,
    OperationsInProgress,
    QueueInfo,
    ResourceInfo,
)

router = APIRouter(prefix="/v1/status", tags=["Gateway Status"])
logger = get_logger(__name__)


@router.get("/detailed", response_model=DetailedStatusResponse)
async def get_detailed_status(
    model_registry=Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
    gateway_config=Depends(get_gateway_config),
):
    """
    Get comprehensive gateway status for proxy orchestration decisions.

    Provides all information needed for:
    - Request routing decisions
    - Load balancing across gateways
    - Resource-based model swapping
    - Queue management

    Returns:
        Detailed status including models, resources, operations, and capacity
    """
    try:
        # Get system resource information
        system_resources = await resource_tracker.get_system_resources()

        # Get all tracked models
        tracked_models = resource_tracker.get_all_models_info()

        # Build model status details
        models_status = {}
        for model_id, info in tracked_models.items():
            models_status[model_id] = ModelStatusDetail(
                status=info.status.value,
                inference_state=getattr(info, "inference_state", None)
                if info.status.value == "busy"
                else None,
                resource_usage=ModelResourceUsage(
                    vram_mb=info.vram_usage_mb, ram_mb=info.ram_usage_mb
                ),
                error_message=info.error_message
                if info.status.value == "error"
                else None,
            )

        # Get operations in progress
        operations = resource_tracker.get_operations_in_progress()

        # Get gateway health and capacity
        busy_models = resource_tracker.get_busy_models()
        concurrent_requests = len(busy_models)

        # TODO: Implement actual queue tracking
        queue_info = QueueInfo(pending_requests=0)

        # TODO: Get actual gateway ID from config
        gateway_id = "gateway-1"

        # Get multi-model capability from injected config
        max_concurrent_workers = gateway_config.process_isolation.max_concurrent_workers
        supports_multi_model = max_concurrent_workers > 1

        # TODO: Implement health check logic based on system state
        gateway_health_status = "healthy"
        if system_resources.available_vram_mb < 1000:  # Less than 1GB available
            gateway_health_status = "degraded"

        return DetailedStatusResponse(
            gateway_id=gateway_id,
            timestamp=time.time(),
            gateway_health=GatewayHealthInfo(
                status=gateway_health_status,
                concurrent_requests=concurrent_requests,
                max_concurrent_requests=0,
                max_concurrent_workers=max_concurrent_workers,
                supports_multi_model=supports_multi_model,
            ),
            resources=ResourceInfo(
                total_vram_mb=system_resources.total_vram_mb,
                available_vram_mb=system_resources.available_vram_mb,
                total_ram_mb=system_resources.total_ram_mb,
                available_ram_mb=system_resources.available_ram_mb,
            ),
            models=models_status,
            operations_in_progress=OperationsInProgress(
                loading=operations["loading"], unloading=operations["unloading"]
            ),
            queue_info=queue_info,
        )

    except Exception as e:
        logger.error(f"Error generating detailed gateway status: {e}")
        raise
