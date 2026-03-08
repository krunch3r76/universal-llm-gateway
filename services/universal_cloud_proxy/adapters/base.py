"""
Provider adapter protocol for cloud APIs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from ..config import ProviderConfig


class ProviderAdapter(Protocol):
    @property
    def adapter_type(self) -> str: ...

    @property
    def config(self) -> ProviderConfig: ...

    @property
    def client(self) -> httpx.AsyncClient: ...

    def normalize_catalog_model_id(self, raw_model_id: str) -> str: ...

    def to_upstream_model_id(self, catalog_model_id: str) -> str: ...

    async def fetch_catalog(self) -> list[dict[str, Any]]: ...

    async def forward_chat(self, request_body: dict[str, Any]) -> dict[str, Any]: ...

    def forward_chat_stream(self, request_body: dict[str, Any]) -> AsyncIterator[bytes]:
        """Forward a streaming chat request and yield provider chunks."""
        ...

    async def forward_embeddings(
        self, request_body: dict[str, Any]
    ) -> dict[str, Any]: ...
