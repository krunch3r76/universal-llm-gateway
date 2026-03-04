"""Cloud proxy metadata passthrough — /api/select, /api/models.

Forwards to cloud proxy over UDS or TCP. Returns 503 when cloud proxy
is not connected. Enforces sole-endpoint invariant: clients use Stargate
:9999, never the cloud proxy port directly.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from ..dependencies import get_proxy

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["cloud-passthrough"])


def _get_cloud_forwarder():
    """Get CloudProxyClient from federation integration, or None."""
    proxy = get_proxy()
    fed = getattr(proxy, "federation_integration", None)
    if not fed:
        return None
    fwd = getattr(fed, "forwarder", None)
    if not fwd:
        return None
    return getattr(fwd, "cloud_forwarder", None)


@router.get("/models")
async def get_models() -> JSONResponse:
    """Forward GET /api/models to cloud proxy. Returns 503 if unavailable."""
    client = _get_cloud_forwarder()
    if not client:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "Cloud proxy not connected",
                    "type": "service_unavailable",
                    "code": "cloud_proxy_unavailable",
                }
            },
        )
    try:
        data = await client.get_models()
        return JSONResponse(content=data)
    except Exception as exc:
        logger.warning("Cloud proxy /api/models failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "Upstream cloud proxy error",
                    "type": "upstream_error",
                    "code": "cloud_proxy_error",
                }
            },
        )


@router.post("/select")
async def select_models(request: Request) -> JSONResponse:
    """Forward POST /api/select to cloud proxy. Returns 503 if unavailable."""
    client = _get_cloud_forwarder()
    if not client:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "Cloud proxy not connected",
                    "type": "service_unavailable",
                    "code": "cloud_proxy_unavailable",
                }
            },
        )
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid JSON in request body",
                    "type": "invalid_request",
                    "code": "bad_request_body",
                }
            },
        )
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Request body must be a JSON object",
                    "type": "invalid_request",
                    "code": "bad_request_body",
                }
            },
        )
    try:
        data = await client.select_models(body)
        return JSONResponse(content=data)
    except Exception as exc:
        logger.warning("Cloud proxy /api/select failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "Upstream cloud proxy error",
                    "type": "upstream_error",
                    "code": "cloud_proxy_error",
                }
            },
        )
