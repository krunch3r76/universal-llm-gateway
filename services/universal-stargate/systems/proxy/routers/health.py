"""Health check endpoint"""

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from universal_logging import get_logger

from ..dependencies import get_optional_auth_dependency, get_proxy
from ..stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
async def health_check(
    request: Request,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, str] = Depends(get_optional_auth_dependency),
):
    """Health check showing proxy and gateway status."""
    # Router-only master: forward_request goes through model-routing which
    # requires catalog (T0: ¬catalog). Check federation liveness directly via
    # heartbeat — independent of catalog state.
    if proxy.federated_manager is not None and proxy.gateway_manager is None:
        live = [
            gw
            for gw in proxy.federated_manager.get_all_gateways()
            if not gw.is_unreachable
        ]
        if live:
            gw = live[0]
            return {
                "status": "healthy",
                "gateway_status": "available",
                "gateways_connected": len(live),
                "vram_free_mb": gw.vram_free_mb,
                "vram_total_mb": gw.vram_total_mb,
                "timestamp": int(time.time()),
                "version": "1.0.0",
            }
        return {
            "status": "stargate_proxy_healthy",
            "gateway_status": "unavailable",
            "message": "Stargate proxy is running but no gateway connected",
            "timestamp": int(time.time()),
            "version": "1.0.0",
        }

    # Edge/standalone: forward to local gateway directly
    try:
        gateway_response = await proxy.forward_request(
            method="GET",
            path="/health",
            headers=dict(request.headers),
            params=dict(request.query_params),
        )
        return gateway_response
    except asyncio.CancelledError:
        raise
    except Exception:
        return {
            "status": "stargate_proxy_healthy",
            "gateway_status": "unavailable",
            "message": "Stargate proxy is running but gateway is temporarily "
            "unavailable",
            "timestamp": int(time.time()),
            "version": "1.0.0",
        }
