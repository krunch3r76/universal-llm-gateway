"""
Cloud provider request forwarding — HTTPS client with auth injection.

Dispatches inference requests to cloud APIs (OpenRouter, etc.) with
Bearer auth headers. Request bodies are passed through as-is
(OpenAI-compatible format). SSE streams are relayed line-by-line.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_APP_TITLE = "Stargate"
_APP_URL = "https://github.com/krunch3r76/universal-llm-gateway"


class ProviderForwarder:
    """HTTPS forwarder for cloud API providers.

    Lifecycle:
        1. Create with ``ProviderForwarder()``
        2. Use ``forward_request()`` / ``forward_request_stream()``
        3. Call ``await close()`` on shutdown
    """

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=300.0, write=15.0, pool=15.0),
            limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
            http2=False,
        )

    async def close(self) -> None:
        await self._client.aclose()
        logger.debug("ProviderForwarder closed")

    def _build_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _APP_URL,
            "X-Title": _APP_TITLE,
        }

    async def forward_request(
        self,
        *,
        base_url: str,
        api_key: str,
        request_body: dict[str, Any],
    ) -> httpx.Response:
        """Forward a non-streaming request to the cloud provider."""
        endpoint = f"{base_url}/chat/completions"
        headers = self._build_headers(api_key)

        logger.debug(
            "Forward (non-stream) → %s model=%s",
            base_url,
            request_body.get("model", "?"),
        )

        response = await self._client.post(endpoint, json=request_body, headers=headers)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            logger.warning("429 rate-limited (Retry-After: %s)", retry_after)
        elif response.status_code >= 500:
            logger.error(
                "%d from provider: %s",
                response.status_code,
                response.text[:300],
            )

        response.raise_for_status()
        return response

    async def forward_request_stream(
        self,
        *,
        base_url: str,
        api_key: str,
        request_body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Forward a streaming request, yielding complete SSE lines as bytes.

        Cloud providers send SSE (``data: {json}\\n\\n``).  We iterate
        with ``aiter_lines()`` which buffers internally and yields at
        line boundaries, preventing mid-line splits.
        """
        endpoint = f"{base_url}/chat/completions"
        headers = self._build_headers(api_key)
        body = {**request_body, "stream": True}

        logger.debug("Forward (stream) → %s model=%s", base_url, body.get("model", "?"))

        async with self._client.stream(
            "POST", endpoint, json=body, headers=headers
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode(errors="replace")[:300]
                logger.error(
                    "Stream %d from provider: %s",
                    response.status_code,
                    error_preview,
                )
                raise httpx.HTTPStatusError(
                    f"Provider returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )

            async for line in response.aiter_lines():
                stripped = line.strip()
                if stripped:
                    yield (stripped + "\n").encode("utf-8")

    async def forward_embedding_request(
        self,
        *,
        base_url: str,
        api_key: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward an embedding request to the cloud provider."""
        endpoint = f"{base_url}/embeddings"
        headers = self._build_headers(api_key)

        logger.debug(
            "Forward (embeddings) → %s model=%s",
            base_url,
            request_body.get("model", "?"),
        )

        response = await self._client.post(endpoint, json=request_body, headers=headers)
        response.raise_for_status()
        return response.json()
