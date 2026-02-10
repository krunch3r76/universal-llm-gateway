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
    try:
        gateway_response = await proxy.forward_request(
            method="GET",
            path="/health",
            headers=dict(request.headers),
            params=dict(request.query_params),
        )
        return gateway_response
    except asyncio.CancelledError:
        raise  # Never swallow cancellation
    except Exception:
        return {
            "status": "stargate_proxy_healthy",
            "gateway_status": "unavailable",
            "message": "Stargate proxy is running but gateway is temporarily unavailable",
            "timestamp": int(time.time()),
            "version": "1.0.0",
        }
