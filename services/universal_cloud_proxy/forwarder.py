"""
Cloud provider request forwarding via provider adapters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from .adapters.base import ProviderAdapter


class ProviderForwarder:
    """Dispatches provider requests to configured adapters."""

    def __init__(
        self,
        *,
        adapters: dict[str, ProviderAdapter],
    ) -> None:
        self._adapters = adapters

    def _adapter(self, provider: str) -> ProviderAdapter:
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise ValueError(f"No adapter configured for provider: {provider}")
        return adapter

    def adapter_type(self, provider: str) -> str:
        """Return configured adapter type for provider."""
        return self._adapter(provider).adapter_type

    async def forward_chat_request(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._adapter(provider).forward_chat(request_body)

    async def forward_request_stream(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        async for chunk in self._adapter(provider).forward_chat_stream(request_body):
            yield chunk

    async def forward_embedding_request(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._adapter(provider).forward_embeddings(request_body)

    async def forward_native(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward provider-native body (no OpenAI translation)."""
        return await self._adapter(provider).forward_native(request_body)

    def forward_native_stream(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Stream provider-native body (no OpenAI translation)."""
        return self._adapter(provider).forward_native_stream(request_body)

    async def forward_image_generation(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST to /images/generations for ``provider``."""
        adapter = self._adapter(provider)
        if not hasattr(adapter, "forward_images_generation"):
            raise ValueError(f"Provider {provider} does not support image generation")
        return await adapter.forward_images_generation(request_body)

    async def forward_image_edit(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST to /images/edits for ``provider``."""
        adapter = self._adapter(provider)
        if not hasattr(adapter, "forward_images_edit"):
            raise ValueError(f"Provider {provider} does not support image editing")
        return await adapter.forward_images_edit(request_body)

    async def forward_video_generation(
        self,
        *,
        provider: str,
        request_body: dict[str, Any],
    ) -> dict[str, Any]:
        """POST to /videos/generations for ``provider``."""
        adapter = self._adapter(provider)
        if not hasattr(adapter, "forward_video_generation"):
            raise ValueError(f"Provider {provider} does not support video generation")
        return await adapter.forward_video_generation(request_body)

    async def forward_video_status(
        self,
        *,
        provider: str,
        request_id: str,
    ) -> dict[str, Any]:
        """GET /videos/{request_id} for ``provider``."""
        adapter = self._adapter(provider)
        if not hasattr(adapter, "forward_video_status"):
            raise ValueError(
                f"Provider {provider} does not support video status polling"
            )
        return await adapter.forward_video_status(request_id)
