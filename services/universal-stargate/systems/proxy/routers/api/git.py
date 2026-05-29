"""HTTP forward proxy for git-integration-worker.

Forwards ``/api/v1/git/*`` verbatim to the worker (default ``http://127.0.0.1:8091``).
Thin relay only — no business logic, no auth re-check (Stargate pass-through).
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response
from transport_utils import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["git-proxy"])

_DEFAULT_HOST = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091"))
_WORKER_BASE_URL = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_PROXY_TIMEOUT = float(os.environ.get("GIT_INTEGRATION_PROXY_TIMEOUT", "600"))

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


@router.api_route(
    "/git/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_git(request: Request, path: str) -> Response:
    """Forward ``/api/v1/git/<path>`` to git-integration-worker."""
    target_path = f"/api/v1/git/{path}"
    forwarded_headers = _filter_request_headers(request.headers.raw)
    body = await request.body()

    client = make_async_client(_WORKER_BASE_URL, timeout=_PROXY_TIMEOUT)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target_path,
            params=request.query_params,
            headers=forwarded_headers,
            content=body,
        )
        upstream = await client.send(upstream_req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.warning(
            "git-integration-worker request failed at %s: %s", _WORKER_BASE_URL, exc
        )
        return Response(
            content=b'{"error":{"code":"git_integration_worker_unavailable",'
            b'"message":"git-integration-worker is not reachable"}}',
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
