"""Google Gemini adapter — native generateContent API and OpenAI-compat chat surface.

Native endpoints use path-segment model routing:
  POST /models/{model}:generateContent
  POST /models/{model}:streamGenerateContent?alt=sse

The OpenAI-compatible surface lives at {base_url}/openai/chat/completions
and is used for forward_chat / forward_chat_stream to avoid duplicating the
OpenAI→Gemini message translation that Google already provides.

Auth: x-goog-api-key header (works for both native and compat endpoints).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, unquote

import httpx
from model_id import ModelId
from universal_logging import get_logger

from ..config import ProviderConfig

logger = get_logger(__name__)


class GoogleAdapter:
    """Proxy requests to the Google Gemini API (native + OpenAI-compat)."""

    def __init__(
        self,
        *,
        config: ProviderConfig,
        client: httpx.AsyncClient,
        event_bus: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._event_bus = event_bus

    @property
    def adapter_type(self) -> str:
        return "google"

    @property
    def config(self) -> ProviderConfig:
        return self._config

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    def _native_headers(self) -> dict[str, str]:
        """Headers for native generateContent endpoints (x-goog-api-key)."""
        return {
            "x-goog-api-key": self._config.api_key,
            "Content-Type": "application/json",
        }

    def _compat_headers(self) -> dict[str, str]:
        """Headers for Google's OpenAI-compatible endpoints (Bearer auth)."""
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        """Normalize Google model names to catalog namespace.

        Google returns ``models/gemini-2.5-flash`` — strip the ``models/``
        prefix and add ``google/``.
        """
        name = raw_model_id
        if name.startswith("models/"):
            name = name[len("models/") :]
        if "/" in name:
            return name
        return f"google/{name}"

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        """Strip google/ prefix — upstream expects bare model names."""
        return ModelId.parse(catalog_model_id).api_model_id

    async def _raise_provider_http_error(self, response: httpx.Response) -> None:
        error_body = await response.aread()
        error_preview = error_body.decode(errors="replace")[:500]
        logger.error(
            "Google Gemini API %d: %s",
            response.status_code,
            error_preview,
        )
        raise httpx.HTTPStatusError(
            f"Provider returned {response.status_code}: {error_preview}",
            request=response.request,
            response=response,
        )

    # ── Catalog ────────────────────────────────────────────────────────────

    async def fetch_catalog(self) -> list[dict[str, Any]]:
        """Fetch available models from Google's models.list endpoint.

        Google returns ``{"models": [...]}``, not ``{"data": [...]}``.
        We transform into the standard catalog entry shape.
        """
        response = await self._client.get(
            f"{self._config.base_url}/models",
            headers=self._native_headers(),
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        raw_models = body.get("models", [])
        if not isinstance(raw_models, list):
            return []
        entries: list[dict[str, Any]] = []
        for m in raw_models:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name", ""))
            methods = m.get("supportedGenerationMethods", [])
            if not isinstance(methods, list) or "generateContent" not in methods:
                continue
            base_id = name.removeprefix("models/")
            entries.append(
                {
                    "id": base_id,
                    "object": "model",
                    "owned_by": "google",
                    "created": 0,
                }
            )
        return entries

    # ── OpenAI-compatible chat (via Google's compat endpoint) ──────────────

    def _prepare_chat_body(
        self,
        request_body: dict[str, Any],
        *,
        stream: bool | None = None,
    ) -> dict[str, Any]:
        model_id = str(request_body.get("model", ""))
        body = {**request_body, "model": self.to_upstream_model_id(model_id)}
        if stream is not None:
            body["stream"] = stream
        return body

    async def forward_chat(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """Forward via Google's OpenAI-compatible chat/completions endpoint."""
        body = self._prepare_chat_body(request_body)
        response = await self._client.post(
            f"{self._config.base_url}/openai/chat/completions",
            json=body,
            headers=self._compat_headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_chat_stream(
        self,
        request_body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Stream via Google's OpenAI-compatible chat/completions endpoint."""
        body = self._prepare_chat_body(request_body, stream=True)
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/openai/chat/completions",
            json=body,
            headers=self._compat_headers(),
        ) as response:
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)
            async for chunk in response.aiter_raw():
                if chunk:
                    yield chunk

    # ── Native Gemini API ──────────────────────────────────────────────────

    def _native_url(self, model: str) -> str:
        """Build ``/models/{model}:generateContent`` URL."""
        return f"{self._config.base_url}/models/{model}:generateContent"

    def _native_stream_url(self, model: str) -> str:
        """Build ``/models/{model}:streamGenerateContent?alt=sse`` URL."""
        return f"{self._config.base_url}/models/{model}:streamGenerateContent?alt=sse"

    def _video_generation_url(self, model: str) -> str:
        """Build Veo ``predictLongRunning`` URL."""
        return f"{self._config.base_url}/models/{model}:predictLongRunning"

    @staticmethod
    def _extract_model(request_body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Extract ``model`` from body and return (model, body_without_model).

        Gemini's native API expects the model in the URL path, not the
        request body.  Our passthrough convention keeps ``model`` in the
        body for routing; the adapter strips it before forwarding.
        """
        model = str(request_body.get("model", ""))
        body = {k: v for k, v in request_body.items() if k != "model"}
        return model, body

    async def forward_native(self, request_body: dict[str, Any]) -> dict[str, Any]:
        """POST Gemini generateContent — native body, model in URL path."""
        model, body = self._extract_model(request_body)
        response = await self._client.post(
            self._native_url(self.to_upstream_model_id(model)),
            json=body,
            headers=self._native_headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        return response.json()

    async def forward_native_stream(
        self,
        request_body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Stream Gemini streamGenerateContent — SSE via ?alt=sse."""
        model, body = self._extract_model(request_body)
        async with self._client.stream(
            "POST",
            self._native_stream_url(self.to_upstream_model_id(model)),
            json=body,
            headers=self._native_headers(),
        ) as response:
            if response.status_code >= 400:
                await self._raise_provider_http_error(response)
            async for chunk in response.aiter_raw():
                if chunk:
                    yield chunk

    # ── Unsupported surfaces ───────────────────────────────────────────────

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("Google Gemini embeddings not yet supported via this adapter")

    async def forward_images_generation(
        self,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Google image generation not yet supported")

    async def forward_images_edit(
        self,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Google image editing not yet supported")

    async def forward_video_generation(
        self,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        model, body = self._extract_model(request_body)
        if not body.get("instances"):
            prompt = body.pop("prompt", None)
            if not prompt:
                raise ValueError("Google video generation requires instances or prompt")
            instance: dict[str, Any] = {"prompt": prompt}
            for source_key, google_key in (
                ("image", "image"),
                ("last_frame", "lastFrame"),
                ("reference_images", "referenceImages"),
                ("video", "video"),
            ):
                value = body.pop(source_key, None)
                if value is not None:
                    instance[google_key] = value
            parameters: dict[str, Any] = {}
            for source_key, google_key in (
                ("aspect_ratio", "aspectRatio"),
                ("duration", "durationSeconds"),
                ("resolution", "resolution"),
                ("person_generation", "personGeneration"),
                ("seed", "seed"),
                ("number_of_videos", "numberOfVideos"),
            ):
                value = body.pop(source_key, None)
                if value is not None:
                    parameters[google_key] = value
            body = {"instances": [instance], **body}
            if parameters:
                body["parameters"] = parameters

        response = await self._client.post(
            self._video_generation_url(self.to_upstream_model_id(model)),
            json=body,
            headers=self._native_headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        result = response.json()
        operation_name = str(result.get("name", ""))
        if not operation_name:
            raise ValueError("Google video generation response missing operation name")
        request_id = quote(operation_name, safe="")
        return {
            **result,
            "id": request_id,
            "request_id": request_id,
            "operation_name": operation_name,
            "status": "pending",
        }

    async def forward_video_status(self, request_id: str) -> dict[str, Any]:
        operation_name = unquote(request_id)
        response = await self._client.get(
            f"{self._config.base_url}/{operation_name}",
            headers=self._native_headers(),
        )
        if response.status_code >= 400:
            await self._raise_provider_http_error(response)
        result = response.json()
        if result.get("done") is True:
            error = result.get("error")
            if isinstance(error, dict):
                return {
                    **result,
                    "request_id": request_id,
                    "operation_name": operation_name,
                    "status": "failed",
                }
            video = _first_google_video(result)
            return {
                **result,
                "request_id": request_id,
                "operation_name": operation_name,
                "status": "succeeded",
                "video": video,
            }
        return {
            **result,
            "request_id": request_id,
            "operation_name": operation_name,
            "status": "pending",
        }


def _first_google_video(result: dict[str, Any]) -> dict[str, Any] | None:
    response = result.get("response")
    if not isinstance(response, dict):
        return None
    generate_response = response.get("generateVideoResponse")
    if not isinstance(generate_response, dict):
        return None
    samples = generate_response.get("generatedSamples")
    if not isinstance(samples, list) or not samples:
        return None
    first = samples[0]
    if not isinstance(first, dict):
        return None
    video = first.get("video")
    return video if isinstance(video, dict) else None
