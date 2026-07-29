"""HTTP forward proxy for git-integration-worker trigger routes.

Forwards ``/api/v1/triggers/*`` verbatim to the worker (default ``8091``).
MCP ``trigger`` relay uses ``STARGATE_URL`` when the worker binds loopback.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response
from transport_utils import make_async_client
from universal_logging import get_logger

from .git import _filter_request_headers, _filter_response_headers

logger = get_logger(__name__)

router = APIRouter(tags=["triggers-proxy"])

_DEFAULT_HOST = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091"))
_WORKER_BASE_URL = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_PROXY_TIMEOUT = float(os.environ.get("GIT_INTEGRATION_PROXY_TIMEOUT", "600"))

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


async def _proxy_triggers(request: Request, *, subpath: str) -> Response:
    target_path = f"/api/v1/triggers/{subpath}" if subpath else "/api/v1/triggers"
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
            "git-integration-worker trigger request failed at %s: %s",
            _WORKER_BASE_URL,
            exc,
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


@router.api_route("/triggers", methods=_PROXY_METHODS)
async def proxy_triggers_root(request: Request) -> Response:
    """Forward ``/api/v1/triggers`` to git-integration-worker."""
    return await _proxy_triggers(request, subpath="")


@router.api_route("/triggers/{path:path}", methods=_PROXY_METHODS)
async def proxy_triggers_path(request: Request, path: str) -> Response:
    """Forward ``/api/v1/triggers/<path>`` to git-integration-worker."""
    return await _proxy_triggers(request, subpath=path)
