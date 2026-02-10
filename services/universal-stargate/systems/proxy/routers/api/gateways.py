"""Gateway management and monitoring endpoints"""

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateways"])


@router.get("/status")
async def gateway_status(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """Get status of all gateways (basic health info)"""
    status = proxy.gateway_manager.get_gateway_status()

    # Add summary
    total = len(status)
    enabled = sum(1 for gw in status.values() if gw["enabled"])
    disabled = total - enabled
    connected = sum(1 for gw in status.values() if gw["is_connected"])

    return {
        "summary": {
            "total_gateways": total,
            "enabled_gateways": enabled,
            "disabled_gateways": disabled,
            "connected_gateways": connected,
            "disconnected_gateways": total - connected,
        },
        "gateways": status,
    }


@router.get("/status/full")
async def gateway_status_full(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """
    Get full status of all gateways including VRAM capacity and models.

    Used by CLI tools to select best gateway for operations like measurement.

    Returns for each gateway:
    - name, url, enabled status, health status
    - total_vram_mb, available_vram_mb (GPU capacity)
    - total_ram_mb, available_ram_mb
    - models: list of model IDs available on this gateway
    """
    # Router-only Master: use federated_manager for federated gateways
    if proxy.gateway_manager is None:
        if proxy.federated_manager is None:
            # No local gateway AND no federation - empty result
            return {
                "summary": {
                    "total_gateways": 0,
                    "enabled_gateways": 0,
                    "disabled_gateways": 0,
                    "connected_gateways": 0,
                    "disconnected_gateways": 0,
                    "best_vram_gateway": None,
                    "best_vram_mb": 0,
                },
                "gateways": {},
            }

        # Use federated_manager for router-only Master
        status = proxy.federated_manager.get_gateway_status_full()
    else:
        # Execution-capable: use gateway_manager for local gateway
        status = proxy.gateway_manager.get_gateway_status_full()

    # Add summary with best gateway for measurement
    total = len(status)
    enabled = sum(1 for gw in status.values() if gw["enabled"])
    disabled = total - enabled
    connected = sum(1 for gw in status.values() if gw["is_connected"])

    # Find gateway with most total VRAM (for measurement capability, must be enabled)
    best_vram_gateway = None
    best_vram = 0
    for url, gw in status.items():
        if gw["enabled"] and gw["is_connected"] and gw["total_vram_mb"] > best_vram:
            best_vram = gw["total_vram_mb"]
            best_vram_gateway = url

    return {
        "summary": {
            "total_gateways": total,
            "enabled_gateways": enabled,
            "disabled_gateways": disabled,
            "connected_gateways": connected,
            "disconnected_gateways": total - connected,
            "best_vram_gateway": best_vram_gateway,
            "best_vram_mb": best_vram,
        },
        "gateways": status,
    }


@router.get("/distribution")
async def model_distribution(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """Get distribution of models across gateways"""
    distribution = proxy.gateway_manager.get_model_distribution()

    # Add statistics
    model_count = len(distribution)
    multi_gateway_models = sum(
        1 for gateways in distribution.values() if len(gateways) > 1
    )

    return {
        "summary": {
            "total_models": model_count,
            "models_on_multiple_gateways": multi_gateway_models,
            "models_on_single_gateway": model_count - multi_gateway_models,
        },
        "distribution": distribution,
    }


@router.post("/{gateway_name}/disable")
async def disable_gateway(
    gateway_name: str,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """
    Disable a gateway at runtime.

    Performs full shutdown:
    - Disconnects WebSocket client
    - Unloads all models (best-effort)
    - Clears model cache entries
    - Marks as disabled for routing and health checks

    The gateway will not receive any new requests until re-enabled.
    """
    result = await proxy.gateway_manager.disable_gateway(gateway_name)

    if not result["success"]:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=result.get("reason", "Unknown error")
        )

    return result


@router.post("/{gateway_name}/enable")
async def enable_gateway(
    gateway_name: str,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """
    Enable a gateway at runtime.

    Reconnects WebSocket client and marks as enabled for routing and health checks.
    The gateway will start receiving requests once connection is established.
    """
    result = await proxy.gateway_manager.enable_gateway(gateway_name)

    if not result["success"]:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400, detail=result.get("reason", "Unknown error")
        )

    return result
