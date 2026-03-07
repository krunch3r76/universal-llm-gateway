from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import ProviderConfig

_APP_TITLE = "Stargate"
_APP_URL = "https://github.com/krunch3r76/universal-llm-gateway"


class OpenAICompatibleAdapter:
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

    def normalize_catalog_model_id(self, raw_model_id: str) -> str:
        provider = self._config.provider.strip().lower()
        if provider == "openrouter":
            return raw_model_id
        if raw_model_id.startswith("native/"):
            return raw_model_id
        if "/" not in raw_model_id:
            if provider == "anthropic":
                return f"native/anthropic/{raw_model_id}"
            if provider in {"openai", "chatgpt"}:
                return f"native/chatgpt/{raw_model_id}"
            return f"{provider}/{raw_model_id}"
        if provider in {"anthropic", "openai", "chatgpt"}:
            return f"native/{raw_model_id}"
        return raw_model_id

    def to_upstream_model_id(self, catalog_model_id: str) -> str:
        provider = self._config.provider.strip().lower()
        prefixes = (
            "native/anthropic/",
            "native/chatgpt/",
            f"{provider}/",
        )
        for prefix in prefixes:
            if catalog_model_id.startswith(prefix):
                return catalog_model_id.removeprefix(prefix)
        return catalog_model_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _APP_URL,
            "X-Title": _APP_TITLE,
        }

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
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    async def forward_chat_stream(
        self, request_body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        body = {
            **request_body,
            "stream": True,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        async with self._client.stream(
            "POST",
            f"{self._config.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                stripped = line.strip()
                if stripped:
                    yield (stripped + "\n").encode()

    async def forward_embeddings(self, request_body: dict[str, Any]) -> dict[str, Any]:
        body = {
            **request_body,
            "model": self.to_upstream_model_id(str(request_body.get("model", ""))),
        }
        response = await self._client.post(
            f"{self._config.base_url}/embeddings",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()
