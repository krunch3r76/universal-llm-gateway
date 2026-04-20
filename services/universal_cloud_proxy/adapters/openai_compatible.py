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
from .responses_bridge import (
    forward_via_responses,
    forward_via_responses_stream,
)

_APP_TITLE = "Stargate"
_APP_URL = "https://github.com/krunch3r76/universal-llm-gateway"

logger = get_logger(__name__)

# OpenAI reasoning-model families reject any non-default value for
# temperature / top_p / presence_penalty / frequency_penalty. Prefix match
# future-proofs against minor revisions (gpt-5.0, gpt-5.1, o4-mini, etc.).
_OPENAI_REASONING_MODEL_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")
_REASONING_UNSUPPORTED_PARAMS: tuple[str, ...] = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
)


def _is_openai_reasoning_model(upstream_model: str) -> bool:
    """True iff the upstream model ID belongs to an OpenAI reasoning family."""
    lowered = upstream_model.lower()
    return any(lowered.startswith(p) for p in _OPENAI_REASONING_MODEL_PREFIXES)


def _strip_reasoning_incompatible_params(
    body: dict[str, Any],
    *,
    provider: str,
    upstream_model: str,
) -> list[str]:
    """Drop params OpenAI reasoning models reject. Returns stripped keys.

    Mutates ``body`` in place. No-op unless ``provider == "openai"`` AND the
    model belongs to a reasoning family; xAI Grok and non-reasoning OpenAI
    models (gpt-4o, gpt-4-turbo, etc.) pass through unchanged.
    """
    if provider != "openai" or not _is_openai_reasoning_model(upstream_model):
        return []
    stripped = [key for key in _REASONING_UNSUPPORTED_PARAMS if key in body]
    for key in stripped:
        body.pop(key, None)
    return stripped


def _emit_strip_debug(upstream_model: str, stripped: list[str], surface: str) -> None:
    """Fire-and-forget debug signal when reasoning-incompatible params were dropped."""
    if not stripped:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called from a sync context with no running loop — debug emission is
        # best-effort; the param strip itself already happened.
        return
    coro = emit_debug_event(
        "debug.cloud.params.stripped",
        {
            "provider": "openai",
            "model": upstream_model,
            "stripped": stripped,
            "surface": surface,
        },
        source="cloud-proxy",
        scope="global",
    )
    loop.create_task(coro)


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

    def _is_mcp_responses_request(self, request_body: dict[str, Any]) -> bool:
        """True when this -mcp request should route via the Responses API.

        OpenAI and xAI support remote MCP on the Responses API but not on
        chat completions.  When ``_mcp_requested`` is set and the provider
        has MCP configured, bridge through /responses.
        """
        provider = self._config.provider.strip().lower()
        if provider not in {"openai", "xai"}:
            return False
        return bool(
            request_body.get("_mcp_requested")
            and self._config.mcp_server_url
            and request_body.get("tool_choice") != "none"
        )

    def _prepare_chat_body(
        self, request_body: dict[str, Any], *, stream: bool | None = None
    ) -> dict[str, Any]:
        model_id = str(request_body.get("model", ""))
        upstream_model = self.to_upstream_model_id(model_id)
        body = {
            **request_body,
            "model": upstream_model,
        }
        if stream is not None:
            body["stream"] = stream
        # GPT-5.x+ rejects max_tokens; the Chat Completions API requires max_completion_tokens.
        if self._config.provider == "openai" and "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        stripped = _strip_reasoning_incompatible_params(
            body, provider=self._config.provider, upstream_model=upstream_model
        )
        _emit_strip_debug(upstream_model, stripped, surface="chat_completions")
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
        """POST Responses API JSON to upstream; strips reasoning-incompatible params for OpenAI reasoning models."""
        upstream_model = str(request_body.get("model", ""))
        body = dict(request_body)
        stripped = _strip_reasoning_incompatible_params(
            body, provider=self._config.provider, upstream_model=upstream_model
        )
        _emit_strip_debug(upstream_model, stripped, surface="responses")
        response = await self._client.post(
            f"{self._config.base_url}/responses",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_native_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Stream Responses API SSE bytes; strips reasoning-incompatible params for OpenAI reasoning models."""
        stream_start = time.monotonic()
        first_chunk_seen = False
        requested_model = str(request_body.get("model", ""))
        body = dict(request_body)
        stripped = _strip_reasoning_incompatible_params(
            body, provider=self._config.provider, upstream_model=requested_model
        )
        _emit_strip_debug(requested_model, stripped, surface="responses_stream")
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/responses",
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
        if self._is_mcp_responses_request(request_body):
            upstream_model = self.to_upstream_model_id(
                str(request_body.get("model", ""))
            )
            return await forward_via_responses(
                self._client, self._config, request_body, upstream_model
            )
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
        """Forward a streaming chat request, translating via Responses API when MCP is active."""
        if self._is_mcp_responses_request(request_body):
            upstream_model = self.to_upstream_model_id(
                str(request_body.get("model", ""))
            )
            async for chunk in forward_via_responses_stream(
                self._client, self._config, request_body, upstream_model
            ):
                yield chunk
            return
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

    async def forward_images_generation(
        self, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        """POST to /images/generations with model ID stripped to upstream form."""
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/images/generations",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_images_edit(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST to /images/edits with model ID stripped to upstream form.

        xAI edits use application/json (not multipart), so the body includes
        ``image`` as an object with ``url`` or ``type`` keys rather than a form
        file upload.
        """
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/images/edits",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_video_generation(
        self, request_body: dict[str, Any]
    ) -> dict[str, Any]:
        """POST to /videos/generations — returns request_id + initial status."""
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/videos/generations",
            json=body,
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_video_status(self, request_id: str) -> dict[str, Any]:
        """GET /videos/{request_id} — poll for completion status."""
        response = await self._client.get(
            f"{self._config.base_url}/videos/{request_id}",
            headers=self._headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()
