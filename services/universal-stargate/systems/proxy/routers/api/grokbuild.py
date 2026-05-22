"""HTTP forward proxy for grokbuild-worker.

Forwards ``/api/v1/grokbuild/*`` requests verbatim to the worker process
(default ``http://127.0.0.1:8090``). This is NOT a FastAPI sub-app mount
— the worker is a separate process with its own lifespan, owned by
``services/grokbuild_worker/``.

Operator-locked invariants:

* **Auth is Stargate pass-through.** This router MUST NOT add an
  ``Authorization`` header, re-check a token, or strip credentials.
  Stargate's edge auth runs *before* this hop; the worker accepts
  whatever arrives.
* **Request preservation.** Method, body, query string, and headers
  (minus hop-by-hop) are forwarded verbatim. 4xx / 5xx responses are
  returned to the caller without retry or remapping.
* **Streaming responses are streamed.** SSE / chunked responses are
  passed through without buffering so future async-build endpoints
  (Phase B) work without router changes.

The proxy preserves the path under ``/api/v1/grokbuild/*`` so the
worker-local URL == the Stargate-proxied URL (no rewriting).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterable

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from transport_utils import make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["grokbuild-proxy"])

_DEFAULT_HOST = os.environ.get("GROKBUILD_WORKER_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.environ.get("GROKBUILD_WORKER_PORT", "8090"))
_WORKER_BASE_URL = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_PROXY_TIMEOUT = float(os.environ.get("GROKBUILD_PROXY_TIMEOUT", "300"))

# Hop-by-hop headers that MUST NOT be forwarded (RFC 7230 §6.1) plus the
# Host header which httpx sets from the target URL.
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
    """Strip hop-by-hop headers; preserve everything else verbatim."""
    out: dict[str, str] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1")
        if name.lower() in _HOP_BY_HOP:
            continue
        out[name] = raw_value.decode("latin-1")
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    """Strip hop-by-hop response headers; preserve everything else."""
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


@router.api_route(
    "/grokbuild/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_grokbuild(request: Request, path: str) -> Response:
    """Forward ``/api/v1/grokbuild/<path>`` to the worker process.

    Streaming is engaged when the upstream response advertises an SSE
    content-type or chunked transfer encoding. All other responses are
    buffered and returned with original headers/status verbatim.
    """
    target_path = f"/api/v1/grokbuild/{path}"
    forwarded_headers = _filter_request_headers(request.headers.raw)
    body = await request.body()

    # ``make_async_client`` is the [universal:transport] entry point; the
    # client gets the worker's TCP base URL (not UDS) but routing through
    # the shared helper keeps timeout/retry/header conventions uniform.
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
        # Broadened from ConnectError to RequestError so read-timeouts,
        # write errors, PoolTimeout, and ProtocolError land on the same
        # structured 503 envelope as connect failures (W3).
        await client.aclose()
        logger.warning(
            "grokbuild-worker request failed at %s: %s", _WORKER_BASE_URL, exc
        )
        return Response(
            content=b'{"error":{"code":"grokbuild_worker_unavailable",'
            b'"message":"grokbuild-worker is not reachable"}}',
            status_code=503,
            media_type="application/json",
        )

    content_type = upstream.headers.get("content-type", "")
    transfer_encoding = upstream.headers.get("transfer-encoding", "")
    is_streaming = (
        "text/event-stream" in content_type or "chunked" in transfer_encoding.lower()
    )

    if is_streaming:

        async def _iter() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            _iter(),
            status_code=upstream.status_code,
            headers=_filter_response_headers(upstream.headers),
            media_type=content_type or None,
        )

    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
        await client.aclose()

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=_filter_response_headers(upstream.headers),
        media_type=content_type or None,
    )
