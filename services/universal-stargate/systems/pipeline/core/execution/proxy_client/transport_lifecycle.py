"""ProxyClient transport lifecycle mixin.

Owns lazy httpx.AsyncClient creation via transport_utils, context manager
protocol, and deferred-close logic that protects in-flight requests.
"""

from __future__ import annotations

from typing import Self

import httpx
from transport_utils import make_async_client
from universal_logging import get_logger

from .configuration import ProxyClientConfig

logger = get_logger(__name__)


class _ProxyTransportLifecycle:
    """Mixin for ProxyClient providing async transport lifecycle.

    Expected instance attributes (set by concrete ProxyClient.__init__):
    - _config: ProxyClientConfig
    - _client: httpx.AsyncClient | None
    - _active_requests: int

    All HTTP operations (chat, embeddings, rerank, cancel) must increment
    _active_requests around their await, and call close() only after
    decrement in finally.
    """

    _config: ProxyClientConfig
    _client: httpx.AsyncClient | None
    _active_requests: int

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized via transport_utils factory."""
        if self._client is None:
            self._client = make_async_client(
                self._config.stargate_url, timeout=self._config.request_timeout
            )
            logger.debug(
                "ProxyClient initialized with stargate_url=%s",
                self._config.stargate_url,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client.

        Defers close if requests are still in flight to avoid StreamClosed
        errors. Logs at ERROR rather than raising — pipeline cleanup calls
        close() from finally blocks, and raising would mask the original error.
        """
        if self._active_requests > 0:
            logger.error(
                "ProxyClient.close() called with %d active request(s) — "
                "deferring close to avoid StreamClosed errors",
                self._active_requests,
            )
            return
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        """Context manager entry - ensure HTTP client exists."""
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - close HTTP client."""
        await self.close()
