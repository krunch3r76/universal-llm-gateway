"""Cloud proxy passthrough — /api/select, /api/models, /api/refresh, /cloud-ui.

Forwards to cloud proxy over UDS or TCP. Returns 503 when cloud proxy
is not connected. Enforces sole-endpoint invariant: clients use Stargate
:9999, never the cloud proxy port directly.

Browser UI is accessible at GET /cloud-ui — static assets are rewritten
to serve under /cloud-ui/static/ so root-relative paths resolve correctly.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
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


@router.post("/refresh")
async def refresh_catalog() -> JSONResponse:
    """Forward POST /api/refresh to cloud proxy. Returns 503 if unavailable."""
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
        response = await client.proxy_request("POST", "/api/refresh")
        return JSONResponse(status_code=response.status_code, content=response.json())
    except Exception as exc:
        logger.warning("Cloud proxy /api/refresh failed: %s", exc)
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


_UI_503 = JSONResponse(
    status_code=503,
    content={
        "error": {
            "message": "Cloud proxy not connected",
            "type": "service_unavailable",
            "code": "cloud_proxy_unavailable",
        }
    },
)

_UI_UPSTREAM_ERROR = JSONResponse(
    status_code=502,
    content={
        "error": {
            "message": "Upstream cloud proxy error",
            "type": "upstream_error",
            "code": "cloud_proxy_error",
        }
    },
)

# Routes outside the /api prefix — registered directly on the module router
_ui_router = APIRouter(tags=["cloud-ui"])


@_ui_router.get("/cloud-ui")
async def cloud_ui_index() -> Response:
    """Serve the cloud proxy model browser UI via Stargate.

    Rewrites root-relative asset paths so /static/... resolves under
    /cloud-ui/static/... while /api/... calls hit the existing Stargate
    passthrough routes unchanged.
    """
    client = _get_cloud_forwarder()
    if not client:
        return _UI_503
    try:
        resp = await client.proxy_request("GET", "/")
        if resp.status_code != 200:
            return Response(content=resp.content, status_code=resp.status_code)
        html = resp.text
        html = html.replace('href="/static/', 'href="/cloud-ui/static/')
        html = html.replace('src="/static/', 'src="/cloud-ui/static/')
        return HTMLResponse(content=html)
    except Exception as exc:
        logger.warning("Cloud proxy UI index failed: %s", exc)
        return _UI_UPSTREAM_ERROR


@_ui_router.get("/cloud-ui/static/{path:path}")
async def cloud_ui_static(path: str) -> Response:
    """Proxy static assets (CSS, JS) from the cloud proxy UI."""
    client = _get_cloud_forwarder()
    if not client:
        return _UI_503
    try:
        resp = await client.proxy_request("GET", f"/static/{path}")
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=content_type,
        )
    except Exception as exc:
        logger.warning("Cloud proxy static asset failed: %s %s", path, exc)
        return _UI_UPSTREAM_ERROR
