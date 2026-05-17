"""
Gateway forwarding for direct topology inference requests.

Contains the pure passthrough forwarders used when an Edge or Master
connects directly to its local Gateway (Unix socket or HTTP URL).

Replaces ad-hoc httpx.AsyncClient(transport=AsyncHTTPTransport(uds=...))
construction with the canonical `make_async_client` from transport_utils.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from src.core.streaming.ndjson_framing import iter_ndjson_lines_bytes

logger = get_logger(__name__)


async def forward_to_gateway_streaming(
    socket_path: str | None,
    http_url: str | None,
    request_body: dict[str, Any],
    request_id: str,
    timeout_hint: float | None = None,
    endpoint: str = "/v1/chat/completions",
) -> AsyncIterator[bytes]:
    """
    Forward streaming inference request to local Gateway via Unix socket or HTTP.

    Yields NDJSON lines transparently without buffering or parsing.

    INVARIANT: ¬buffer ∧ ¬parse ∧ ¬modify (pure passthrough)

    Args:
        socket_path: Unix socket path to gateway (if socket-based)
        http_url: HTTP URL to gateway (if HTTP-based)
        request_body: OpenAI-compatible request (model, messages, stream=True, etc.)
        request_id: Request ID for tracing
        timeout_hint: Explicit timeout in seconds (None = no worker-level timeout)
        endpoint: Gateway endpoint path (default: /v1/chat/completions)

    Yields:
        Raw bytes from SSE stream

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx response
        httpx.RequestError: On connection failure
    """
    if socket_path:
        target_url = f"unix://{socket_path}"
        inference_endpoint = f"http://gateway{endpoint}"
    elif http_url:
        target_url = http_url.rstrip("/")
        inference_endpoint = f"{target_url}{endpoint}"
    else:
        raise ValueError("Either socket_path or http_url must be provided")

    async with make_async_client(target_url, timeout=10.0) as client:
        conn_type = "socket" if socket_path else "HTTP"
        logger.debug(
            f"Forwarding streaming request to Gateway via {conn_type}",
            extra={"request_id": request_id},
        )

        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Internal-Request-ID": request_id,
        }
        if timeout_hint is not None:
            headers["X-Request-Timeout"] = str(timeout_hint)

        # Apply granular timeout at request level (read=None for indefinite streams)
        req_timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        async with client.stream(
            "POST",
            inference_endpoint,
            json=request_body,
            headers=headers,
            timeout=req_timeout,
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode()[:500]
                raise httpx.HTTPStatusError(
                    f"Gateway returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            async for framed_line in iter_ndjson_lines_bytes(response):
                yield framed_line


async def forward_to_gateway_nonstreaming(
    socket_path: str | None,
    http_url: str | None,
    request_body: dict[str, Any],
    request_id: str,
    timeout_hint: float | None = None,
    endpoint: str = "/v1/chat/completions",
) -> dict[str, Any]:
    """
    Forward non-streaming inference request to local Gateway via Unix socket or HTTP.

    INVARIANT: Pure passthrough - Gateway response returned directly

    Args:
        socket_path: Unix socket path to gateway (if socket-based)
        http_url: HTTP URL to gateway (if HTTP-based)
        request_body: OpenAI-compatible request (model, messages, stream=False, etc.)
        request_id: Request ID for tracing
        timeout_hint: Explicit timeout in seconds (None = no worker-level timeout)
        endpoint: Gateway endpoint path (default: /v1/chat/completions)

    Returns:
        Gateway response (OpenAI-compatible chat completion response)

    Raises:
        httpx.HTTPStatusError: On 4xx/5xx response
        httpx.RequestError: On connection failure
    """
    if socket_path:
        target_url = f"unix://{socket_path}"
        inference_endpoint = f"http://gateway{endpoint}"
    elif http_url:
        target_url = http_url.rstrip("/")
        inference_endpoint = f"{target_url}{endpoint}"
    else:
        raise ValueError("Either socket_path or http_url must be provided")

    async with make_async_client(target_url, timeout=10.0) as client:
        conn_type = "socket" if socket_path else "HTTP"
        logger.debug(
            f"Forwarding non-streaming request to Gateway via {conn_type}",
            extra={"request_id": request_id},
        )

        headers = {
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Internal-Request-ID": request_id,
        }
        if timeout_hint is not None:
            headers["X-Request-Timeout"] = str(timeout_hint)

        # Apply granular timeout at request level
        req_timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        response = await client.post(
            inference_endpoint,
            json=request_body,
            headers=headers,
            timeout=req_timeout,
        )
        _ = response.raise_for_status()
        return response.json()
