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
