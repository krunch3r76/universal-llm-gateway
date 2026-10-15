"""GET-only Stargate relay to the claudeburst perps read API (:8891).

Cross-host readers use this route with Stargate bearer auth. Upstream perps REST
has no app-layer auth on loopback; this layer enforces GET + path allowlist.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from transport_utils import make_async_client
from universal_logging import get_logger

from systems.proxy.dependencies import get_auth_dependency

from .git import _filter_request_headers, _filter_response_headers

logger = get_logger(__name__)

router = APIRouter(tags=["claudeburst-read"])

_DEFAULT_HOST = os.environ.get("CLAUDEBURST_PERPS_HOST", "127.0.0.1").strip()
_DEFAULT_PORT = int(os.environ.get("CLAUDEBURST_PERPS_PORT", "8891"))
_UPSTREAM_BASE = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_PROXY_TIMEOUT = float(os.environ.get("CLAUDEBURST_READ_PROXY_TIMEOUT", "30"))

# K38 Step 1 allowlist — prefix match on upstream path (leading slash required).
_ALLOWED_GET_PREFIXES: tuple[str, ...] = (
    "/trades",
    "/pnl",
    "/risk",
    "/signals",
    "/heartbeats",
    "/position-geometry",
    "/price-history/ohlcv",
    "/price-history/health",
    "/ledger/integrity-audit",
)


def _path_allowed(path: str) -> bool:
    normalized = "/" + path.lstrip("/").split("?", 1)[0]
    return any(normalized == prefix for prefix in _ALLOWED_GET_PREFIXES)


def allowed_get_prefixes() -> tuple[str, ...]:
    """Expose allowlist for tests."""
    return _ALLOWED_GET_PREFIXES


async def _proxy_get(request: Request, *, subpath: str) -> Response:
    if request.method != "GET":
        raise HTTPException(status_code=405, detail="only GET is allowed")
    target_path = f"/{subpath}" if subpath else "/"
    if not _path_allowed(target_path.split("?", 1)[0]):
        raise HTTPException(status_code=404, detail="path not in claudeburst read allowlist")

    forwarded_headers = _filter_request_headers(request.headers.raw)
    client = make_async_client(_UPSTREAM_BASE, timeout=_PROXY_TIMEOUT)
    try:
        upstream_req = client.build_request(
            method="GET",
            url=target_path,
            params=request.query_params,
            headers=forwarded_headers,
        )
        upstream = await client.send(upstream_req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.warning("claudeburst perps read proxy failed: %s", exc)
        return Response(
            content=b'{"error":{"code":"claudeburst_perps_unavailable"}}',
            status_code=503,
            media_type="application/json",
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


@router.get("/claudeburst/read")
async def claudeburst_read_root(
    request: Request,
    _auth: dict = Depends(get_auth_dependency),
) -> Response:
    del _auth
    return await _proxy_get(request, subpath="")


@router.api_route("/claudeburst/read/{path:path}", methods=["GET"])
async def claudeburst_read_path(
    request: Request,
    path: str,
    _auth: dict = Depends(get_auth_dependency),
) -> Response:
    del _auth
    return await _proxy_get(request, subpath=path)


@router.api_route("/claudeburst/read/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def claudeburst_read_reject_mutations(path: str) -> Response:
    del path
    raise HTTPException(status_code=405, detail="only GET is allowed")
