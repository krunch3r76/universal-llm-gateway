"""
Cloud proxy client — forwards requests to the cloud proxy over loopback.

Replaces direct HTTPS to cloud providers. The proxy handles auth
injection and provider communication; this client just relays
OpenAI-format requests and SSE responses over the local network.

INVARIANT: ¬ API keys ∧ ¬ outbound HTTPS — proxy is trusted loopback
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)


def parse_cloud_proxy_url(url: str) -> tuple[str | None, str]:
    """Parse proxy URL into (uds_path, base_url).

    For unix:///path: (path, "http://localhost")
    For http://host:port: (None, full_url)
    """
    url = url.strip().rstrip("/")
    if url.startswith("unix://"):
        path = url[7:].lstrip("/")
        return (
            f"/{path}" if path else "/tmp/universal-protocol/cloud-proxy.sock",
            "http://localhost",
        )
    return None, url


class CloudProxyClient:
    """HTTP client targeting the cloud proxy service over loopback.

    Supports UDS (unix://) and TCP (http://). Lifecycle:
        1. Create with ``CloudProxyClient(proxy_url)``
        2. Use ``forward_request()`` / ``forward_request_stream()``
        3. Call ``await close()`` on shutdown
    """

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        uds_path, base_url = parse_cloud_proxy_url(proxy_url)
        timeout = httpx.Timeout(
            connect=10.0,
            read=300.0,
            write=10.0,
            pool=10.0,
        )
        limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
        if uds_path:
            transport = httpx.AsyncHTTPTransport(uds=uds_path)
            self._client = httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                timeout=timeout,
                limits=limits,
            )
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                limits=limits,
            )

    @property
    def proxy_mode(self) -> str:
        """Transport mode: 'uds' for unix://, 'tcp' for http://."""
        return "uds" if self._proxy_url.startswith("unix://") else "tcp"

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.debug("CloudProxyClient closed")

    async def forward_request(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> httpx.Response:
        """Forward a non-streaming request via the cloud proxy.

        The request body is sent as-is (OpenAI chat/completions format).
        The proxy adds auth headers and forwards to the provider.
        """
        path = "/v1/chat/completions"
        logger.debug(
            "Proxy forward (non-stream) model=%s",
            request_body.get("model", "?"),
            extra={"request_id": request_id},
        )

        response = await self._client.post(
            path, json=request_body, headers={"Content-Type": "application/json"}
        )

        if response.status_code >= 400:
            error_preview = response.text[:300]
            logger.error(
                "Proxy %d: %s",
                response.status_code,
                error_preview,
                extra={"request_id": request_id},
            )
            raise httpx.HTTPStatusError(
                f"Proxy returned {response.status_code}: {error_preview}",
                request=response.request,
                response=response,
            )

        return response

    async def forward_request_stream(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> AsyncIterator[bytes]:
        """Forward a streaming request via the cloud proxy.

        Yields complete SSE lines as bytes, same interface as the
        previous direct cloud forwarder for drop-in compatibility with
        FederatedRequestForwarder.
        """
        path = "/v1/chat/completions"
        body = {**request_body, "stream": True}
        logger.debug(
            "Proxy forward (stream) model=%s",
            body.get("model", "?"),
            extra={"request_id": request_id},
        )

        async with self._client.stream(
            "POST",
            path,
            json=body,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode(errors="replace")[:300]
                logger.error(
                    "Proxy stream %d: %s",
                    response.status_code,
                    error_preview,
                    extra={"request_id": request_id},
                )
                raise httpx.HTTPStatusError(
                    f"Proxy returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            async for line in response.aiter_lines():
                stripped = line.strip()
                if stripped:
                    yield (stripped + "\n").encode("utf-8")

    async def forward_embedding_request(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """Forward an embedding request via the cloud proxy."""
        path = "/v1/embeddings"
        logger.debug(
            "Proxy forward (embeddings) model=%s",
            request_body.get("model", "?"),
            extra={"request_id": request_id},
        )

        response = await self._client.post(
            path, json=request_body, headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()

    async def get_models(self) -> dict[str, Any]:
        """GET /api/models — full OpenRouter catalog with pricing."""
        response = await self._client.get("/api/models")
        response.raise_for_status()
        return response.json()

    async def select_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/select — select models by capability tags and context."""
        response = await self._client.post(
            "/api/select",
            json=payload or {},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()
