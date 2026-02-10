"""
HTTP client for llama-server.

Provides high-level interface for:
- Text completion
- Chat completion
- Streaming
- Embeddings (requires --embedding mode)
- Model management (router mode)
- Token counting
"""

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)


class LlamaServerClient:
    """
    HTTP client for llama-server.

    Provides high-level interface for:
    - Text completion
    - Chat completion
    - Streaming
    - Embeddings (requires --embedding mode)
    - Model management (router mode)
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 600.0,
        socket_path: str | None = None,
    ):
        """Initialize client.

        Args:
            base_url: Base URL of llama-server (e.g., http://localhost:8080)
            timeout: Default timeout for requests
            socket_path: Unix socket path (preferred over TCP when set)
        """
        self.base_url = base_url
        self.timeout = timeout
        self._socket_path = socket_path
        self._client: httpx.AsyncClient | None = None

    def _build_client(self) -> httpx.AsyncClient:
        """Create httpx async client with appropriate transport."""
        if self._socket_path:
            transport = httpx.AsyncHTTPTransport(uds=self._socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=self.timeout,
            )
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = self._build_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client, creating if needed."""
        if not self._client:
            self._client = self._build_client()
        return self._client

    async def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST request to server endpoint."""
        response = await self.client.post(endpoint, json=body)
        response.raise_for_status()
        return response.json()

    async def _post_stream(
        self, endpoint: str, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POST request with SSE streaming."""
        async with self.client.stream(
            "POST",
            endpoint,
            json=body,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse SSE data: {data}")

    async def chat_completions(
        self, messages: list[dict[str, str]], **params: Any
    ) -> dict[str, Any]:
        """POST /v1/chat/completions (non-streaming)."""
        body = {"messages": messages, **params}
        return await self._post("/v1/chat/completions", body)

    async def chat_completions_stream(
        self, messages: list[dict[str, str]], **params: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POST /v1/chat/completions (streaming SSE)."""
        body = {"messages": messages, **params}
        async for chunk in self._post_stream("/v1/chat/completions", body):
            yield chunk

    async def completions(self, prompt: str, **params: Any) -> dict[str, Any]:
        """POST /v1/completions (non-streaming)."""
        body = {"prompt": prompt, **params}
        return await self._post("/v1/completions", body)

    async def completions_stream(
        self, prompt: str, **params: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POST /v1/completions (streaming SSE)."""
        body = {"prompt": prompt, **params}
        async for chunk in self._post_stream("/v1/completions", body):
            yield chunk

    async def embeddings(
        self,
        input_texts: list[str] | str,
        **params: Any,
    ) -> dict[str, Any]:
        """POST /v1/embeddings — generate text embeddings.

        Args:
            input_texts: Single string or list of strings to embed
            **params: Additional parameters (encoding_format, etc.)

        Returns:
            OpenAI-compatible embedding response
        """
        body: dict[str, Any] = {"input": input_texts, **params}
        return await self._post("/v1/embeddings", body)

    async def tokenize(self, text: str) -> list[int]:
        """POST /tokenize — accurate token counting."""
        result = await self._post("/tokenize", {"content": text})
        return result["tokens"]

    async def list_models(self) -> dict[str, Any]:
        """
        List available models (router mode).

        Returns:
            Models list with status (loaded, loading, unloaded)
        """
        response = await self.client.get("/models")
        response.raise_for_status()
        return response.json()

    async def load_model(self, model: str) -> dict[str, Any]:
        """
        Manually load model (router mode).

        Args:
            model: Model ID to load

        Returns:
            Load status response
        """
        response = await self.client.post(
            "/models/load",
            json={"model": model},
        )
        response.raise_for_status()
        return response.json()

    async def unload_model(self, model: str) -> dict[str, Any]:
        """
        Manually unload model (router mode).

        Args:
            model: Model ID to unload

        Returns:
            Unload status response
        """
        response = await self.client.post(
            "/models/unload",
            json={"model": model},
        )
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        """
        Check server health.

        Returns:
            Health status
        """
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()
