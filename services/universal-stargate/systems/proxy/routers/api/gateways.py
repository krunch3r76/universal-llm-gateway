"""Gateway management and monitoring endpoints"""

from fastapi import APIRouter, Depends
from universal_logging import get_logger

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy
from ...vram_selection import select_best_vram_from_status

logger = get_logger(__name__)
router = APIRouter(prefix="/gateways", tags=["gateways"])


def _model_distribution_from_status_full(status: dict) -> dict[str, list]:
    """Invert full status rows into model id → gateway id lists.

    FederatedGatewayManager exposes get_gateway_status_full and not
    get_model_distribution. Each row's models list is the catalog already
    used by /gateways/status/full.
    """
    distribution: dict[str, list] = {}
    for gateway_id, row in status.items():
        for model_id in row.get("models") or []:
            distribution.setdefault(str(model_id), []).append(gateway_id)
    return distribution


@router.get("/status")
async def gateway_status(
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """Return basic health for every gateway this proxy can see.

    A router-only master has gateway_manager None. Status rows then come
    from federated_manager.get_gateway_status_full, because that class has
    no get_gateway_status. When both managers are absent the handler returns
    a zeroed summary and an empty gateways map instead of raising. An
    execution-capable proxy still calls gateway_manager.get_gateway_status.
    """
    # Router-only Master: use federated_manager for federated gateways
    if proxy.gateway_manager is None:
        if proxy.federated_manager is None:
            return {
                "summary": {
                    "total_gateways": 0,
                    "enabled_gateways": 0,
                    "disabled_gateways": 0,
                    "connected_gateways": 0,
                    "disconnected_gateways": 0,
                },
                "gateways": {},
            }
        status = proxy.federated_manager.get_gateway_status_full()
    else:
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

    best_vram_gateway, best_vram = select_best_vram_from_status(status)

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
    """Return which gateways serve each model, with single-vs-multi counts.

    Same manager guard as gateway_status. FederatedGatewayManager has no
    get_model_distribution, so a router-only master builds the map from
    models lists on get_gateway_status_full rows. Both managers absent
    yields a zero summary and an empty distribution. An execution-capable
    proxy still calls gateway_manager.get_model_distribution.
    """
    if proxy.gateway_manager is None:
        if proxy.federated_manager is None:
            return {
                "summary": {
                    "total_models": 0,
                    "models_on_multiple_gateways": 0,
                    "models_on_single_gateway": 0,
                },
                "distribution": {},
            }
        distribution = _model_distribution_from_status_full(
            proxy.federated_manager.get_gateway_status_full()
        )
    else:
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
