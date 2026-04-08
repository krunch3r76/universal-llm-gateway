from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from model_id import ModelId
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

from ..config import ProviderConfig

_APP_TITLE = "Stargate"
_APP_URL = "https://github.com/krunch3r76/universal-llm-gateway"

logger = get_logger(__name__)


class OpenAICompatibleAdapter:
    """Forward OpenAI-compatible provider requests while preserving model-ID mapping and upstream error semantics."""

    def __init__(self, *, config: ProviderConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    @property
    def adapter_type(self) -> str:
        return "openai_compatible"

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _emit_stream_debug(
        self,
        *,
        step: str,
        model_id: str,
        stream_start: float,
        chunk_bytes: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "step": step,
            "model_id": model_id,
            "provider": self._config.provider,
            "elapsed_ms": round((time.monotonic() - stream_start) * 1000.0, 1),
        }
        if chunk_bytes is not None:
            payload["chunk_bytes"] = chunk_bytes
        asyncio.create_task(
            emit_debug_event(
                "debug.cloud.stream",
                payload,
                source="cloud-proxy",
                scope="global",
            )
        )

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        """Normalize provider model IDs into the catalog namespace.

        OpenRouter: prefix with openrouter/ so IDs are unambiguous.
        Native providers: bare provider/model (already the canonical form).
        Bare model names (no slash): prefix with provider name.
        """
        provider = self._config.provider.strip().lower()
        if provider == "openrouter":
            if raw_model_id.startswith("openrouter/"):
                return raw_model_id
            return f"openrouter/{raw_model_id}"
        if "/" in raw_model_id:
            return raw_model_id
        return f"{provider}/{raw_model_id}"

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        """Strip catalog namespace back to the ID the upstream API expects.

        OpenRouter expects provider/model. Native providers expect bare model name.
        """
        return ModelId.parse(catalog_model_id).api_model_id

    def _prepare_chat_body(
        self, request_body: dict[str, Any], *, stream: bool | None = None
    ) -> dict[str, Any]:
        model_id = str(request_body.get("model", ""))
        body = {
            **request_body,
            "model": self.to_upstream_model_id(model_id),
        }
        if stream is not None:
            body["stream"] = stream
        return body

    async def _forward_chat_passthrough_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Stream provider chat-completions bytes unchanged.

        This path is a transparent SSE relay. It preserves the upstream framing
        exactly as received so downstream clients observe the same event
        boundaries and flush behavior as the provider emitted.
        """
        requested_model = str(request_body.get("model", ""))
        body = self._prepare_chat_body(request_body, stream=True)
        stream_start = time.monotonic()
        first_chunk_seen = False
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        ) as response:
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)
            async for chunk in response.aiter_raw():
                if chunk:
                    if not first_chunk_seen:
                        first_chunk_seen = True
                        self._emit_stream_debug(
                            step="firstchunk",
                            model_id=requested_model,
                            stream_start=stream_start,
                            chunk_bytes=len(chunk),
                        )
                    yield chunk

    async def forward_native(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST Responses API JSON unchanged (native xAI/OpenAI-shaped ingress)."""
        response = await self._client.post(
            f"{self._config.base_url}/responses",
            json=request_body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _APP_URL,
            "X-Title": _APP_TITLE,
        }

    async def _raise_provider_http_error(self, response: httpx.Response) -> None:
        """Raise HTTPStatusError with provider response body preserved for diagnostics."""
        error_body = await response.aread()
        error_preview = error_body.decode(errors="replace")[:500]
        logger.error(
            "OpenAI-compatible provider API %d: %s",
            response.status_code,
            error_preview,
        )
        raise httpx.HTTPStatusError(
            f"Provider returned {response.status_code}: {error_preview}",
            request=response.request,
            response=response,
        )

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self._config.base_url}/models",
            headers=self._headers(),
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        data = body.get("data", [])
        return data if isinstance(data, list) else []

    async def forward_chat(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Forward a non-streaming chat request and preserve provider error payloads on HTTP failure."""
        body = self._prepare_chat_body(request_body)
        response = await self._client.post(
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_chat_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Forward a streaming chat request as a transparent SSE relay."""
        async for chunk in self._forward_chat_passthrough_stream(request_body):
            yield chunk

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Forward an embeddings request and preserve provider error payloads on upstream HTTP failure."""
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/embeddings",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()
