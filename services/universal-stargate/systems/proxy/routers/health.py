"""Health check endpoint"""

import asyncio
import time

from deploy_identity.code_version import resolve_code_version
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
    pipeline_ready = proxy.is_pipeline_system_ready
    pipeline_count = (
        len(proxy.pipeline_registry.pipelines)
        if proxy.pipeline_registry is not None
        else 0
    )
    code_version = resolve_code_version()

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
            status = "healthy" if pipeline_ready else "stargate_proxy_healthy"
            response = {
                "status": status,
                "gateway_status": "available",
                "pipeline_system_ready": pipeline_ready,
                "pipeline_count": pipeline_count,
                "gateways_connected": len(live),
                "vram_free_mb": gw.vram_free_mb,
                "vram_total_mb": gw.vram_total_mb,
                "timestamp": int(time.time()),
                "version": "1.0.0",
                "code_version": code_version,
            }
            if not pipeline_ready:
                response["message"] = (
                    "Stargate proxy is running but pipeline execution is not ready yet"
                )
            return response
        return {
            "status": "stargate_proxy_healthy",
            "gateway_status": "unavailable",
            "pipeline_system_ready": pipeline_ready,
            "pipeline_count": pipeline_count,
            "message": "Stargate proxy is running but no gateway connected",
            "timestamp": int(time.time()),
            "version": "1.0.0",
            "code_version": code_version,
        }

    # Edge/standalone: forward to local gateway directly
    try:
        gateway_response = await proxy.forward_request(
            method="GET",
            path="/health",
            headers=dict(request.headers),
            params=dict(request.query_params),
        )
        if hasattr(gateway_response, "body"):
            try:
                payload = gateway_response.json()
            except Exception:  # pragma: no cover - defensive parsing
                payload = {}
            if isinstance(payload, dict):
                payload["pipeline_system_ready"] = pipeline_ready
                payload["pipeline_count"] = pipeline_count
                # Stargate process identity — not the nested gateway SHA.
                payload["code_version"] = code_version
                if payload.get("status") == "healthy" and not pipeline_ready:
                    payload["status"] = "stargate_proxy_healthy"
                    payload["message"] = (
                        "Gateway is healthy but Stargate pipeline "
                        "execution is not ready yet"
                    )
                return payload
        return {
            "status": "healthy" if pipeline_ready else "stargate_proxy_healthy",
            "gateway_status": "available",
            "pipeline_system_ready": pipeline_ready,
            "pipeline_count": pipeline_count,
            "timestamp": int(time.time()),
            "version": "1.0.0",
            "code_version": code_version,
        }
    except asyncio.CancelledError:
        raise
    except Exception:
        return {
            "status": "stargate_proxy_healthy",
            "gateway_status": "unavailable",
            "pipeline_system_ready": pipeline_ready,
            "pipeline_count": pipeline_count,
            "message": "Stargate proxy is running but gateway is temporarily "
            "unavailable",
            "timestamp": int(time.time()),
            "version": "1.0.0",
            "code_version": code_version,
        }
