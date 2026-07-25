"""Stargate-facing native cursor API — passthrough to git-integration-worker.

Registers GIW ``POST /api/v1/cursor/dispatch`` (and related catalog) as a
provider peer of ``/api/v1/providers/{anthropic|xai|…}``. Request SoT:
``services/git_integration_worker/models/cursor_api.CursorDispatchRequest``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from transport_utils import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/providers/cursor", tags=["provider-native-cursor"])

def _worker_base_url() -> str:
    host = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
    port = int(os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091"))
    return f"http://{host}:{port}"


_PROXY_TIMEOUT = float(os.environ.get("GIT_INTEGRATION_PROXY_TIMEOUT", "600"))
# Strip credential headers — GIW authenticates independently; Stargate caller
# auth must not be forwarded (asymmetric with CDP loopback-trusted satellite).
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "authorization",
        "cookie",
    }
)


def _filter_request_headers(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1")
        if name.lower() in _HOP_BY_HOP:
            continue
        out[name] = raw_value.decode("latin-1")
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


async def _relay_to_giw(request: Request, worker_path: str) -> Response:
    forwarded_headers = _filter_request_headers(request.headers.raw)
    body = await request.body()
    base = _worker_base_url()
    client = make_async_client(base, timeout=_PROXY_TIMEOUT)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=worker_path,
            params=request.query_params,
            headers=forwarded_headers,
            content=body,
        )
        upstream = await client.send(upstream_req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.warning(
            "cursor native relay failed at %s%s: %s",
            base,
            worker_path,
            exc,
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "cursor_worker_unavailable",
                    "message": "git-integration-worker is not reachable",
                }
            },
        )
    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()
    content_type = upstream.headers.get("content-type", "")
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=content_type or None,
    )


@router.post("/dispatch")
async def cursor_native_dispatch(request: Request) -> Response:
    """Native cursor dispatch — proxies GIW ``POST /api/v1/cursor/dispatch``."""
    return await _relay_to_giw(request, "/api/v1/cursor/dispatch")


@router.get("/catalog")
async def cursor_native_catalog(request: Request) -> Response:
    """Native cursor catalog — proxies GIW ``GET /api/v1/cursor/catalog``."""
    return await _relay_to_giw(request, "/api/v1/cursor/catalog")
