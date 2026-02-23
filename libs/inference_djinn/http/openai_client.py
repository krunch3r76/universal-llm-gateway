"""
Generic HTTP client for OpenAI-compatible inference servers.

Provides high-level interface for chat completions, streaming, and embeddings.
Used by both llama-server (GGUF) and vllm serve backends.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import httpx
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


def _is_context_length_overflow(response: httpx.Response) -> bool:
    """Detect vLLM's 400 when input_tokens + max_tokens > context_length.

    vLLM error shape:
      {"error": {"message": "You passed X input tokens and requested Y output tokens.
       However, the model's context length is only Z tokens...",
       "param": "input_tokens", "code": 400}}
    """
    if response.status_code != 400:
        return False
    try:
        body = response.json()
        error = body.get("error", {})
        return (
            error.get("param") == "input_tokens"
            and "context" in error.get("message", "").lower()
        )
    except Exception:
        return False


class OpenAIServerClient:
    """
    HTTP client for any OpenAI-compatible server (llama-server, vllm serve, etc.).

    Provides:
    - Chat completion (streaming and non-streaming)
    - Text completion
    - Embeddings
    - Health check
    - Optional: tokenize, model management (server-specific)
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 600.0,
        socket_path: str | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """
        Args:
            base_url: Base URL (e.g., http://localhost:8080)
            timeout: Request timeout in seconds
            socket_path: Unix socket path (overrides base_url when set)
            event_bus: Optional event bus for structured observability.
        """
        self.base_url = base_url
        self.timeout = timeout
        self._socket_path = socket_path
        self._event_bus = event_bus
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

    async def __aenter__(self) -> OpenAIServerClient:
        """Async context manager entry."""
        self._client = self._build_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Get HTTP client, creating if needed."""
        if not self._client:
            self._client = self._build_client()
        return self._client

    def _emit_context_overflow_retry(self, endpoint: str, max_tokens: int) -> None:
        """Emit context overflow retry event or log warning."""
        if self._event_bus:
            from .events import ContextOverflowRetried

            self._event_bus.publish_async_nowait(
                ContextOverflowRetried(
                    endpoint=endpoint, original_max_tokens=max_tokens
                )
            )
        else:
            logger.warning(
                "Context overflow with max_tokens=%s on %s — retrying without "
                "(vLLM tokenizer discrepancy)",
                max_tokens,
                endpoint,
            )

    async def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST request to server endpoint."""
        response = await self.client.post(endpoint, json=body)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if "max_tokens" in body and _is_context_length_overflow(e.response):
                self._emit_context_overflow_retry(endpoint, body["max_tokens"])
                retry_body = {k: v for k, v in body.items() if k != "max_tokens"}
                return await self._post(endpoint, retry_body)
            raise httpx.HTTPStatusError(
                f"{e} — body: {response.text}",
                request=e.request,
                response=e.response,
            ) from e
        return response.json()

    async def _post_stream(
        self, endpoint: str, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """POST request with SSE streaming."""
        async with self.client.stream("POST", endpoint, json=body) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                body_text = await response.aread()
                if "max_tokens" in body and _is_context_length_overflow(e.response):
                    self._emit_context_overflow_retry(endpoint, body["max_tokens"])
                    retry_body = {k: v for k, v in body.items() if k != "max_tokens"}
                    async for chunk in self._post_stream(endpoint, retry_body):
                        yield chunk
                    return
                raise httpx.HTTPStatusError(
                    f"{e} — body: {body_text.decode(errors='replace')}",
                    request=e.request,
                    response=e.response,
                ) from e
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
        self, messages: list[dict[str, Any]], **params: Any
    ) -> dict[str, Any]:
        """POST /v1/chat/completions (non-streaming)."""
        body: dict[str, Any] = {"messages": messages, **params}
        return await self._post("/v1/chat/completions", body)

    async def chat_completions_stream(
        self, messages: list[dict[str, Any]], **params: Any
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
        """POST /v1/embeddings — generate text embeddings."""
        body: dict[str, Any] = {"input": input_texts, **params}
        return await self._post("/v1/embeddings", body)

    async def tokenize(self, text: str, *, model: str | None = None) -> list[int]:
        """POST /tokenize — token counting (raw text).

        Args:
            text: Text to tokenize.
            model: When set, uses vLLM format `{"model": ..., "prompt": ...}`.
                   When None, uses llama-server format `{"content": ...}`.
        """
        if model:
            payload = {"model": model, "prompt": text}
        else:
            payload = {"content": text}
        result = await self._post("/tokenize", payload)
        return result["tokens"]

    async def tokenize_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        add_generation_prompt: bool = True,
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        """POST /tokenize with chat template applied — accurate count for chat models.

        Uses the messages format so the server applies its chat template before
        tokenizing, matching the actual token count used during inference.

        Args:
            messages: Chat messages (same format as /v1/chat/completions).
            model: Model identifier (required for vLLM).
            add_generation_prompt: Append the generation prompt prefix (default True).
            tools: Tool definitions — included so the chat template expansion
                accounts for their token cost.

        Returns:
            Token count after applying the chat template.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "add_generation_prompt": add_generation_prompt,
        }
        if tools:
            payload["tools"] = tools
        result = await self._post("/tokenize", payload)
        return result["count"]

    async def list_models(self) -> dict[str, Any]:
        """GET /models — list models (llama-server router mode)."""
        response = await self.client.get("/models")
        response.raise_for_status()
        return response.json()

    async def load_model(self, model: str) -> dict[str, Any]:
        """POST /models/load — load model (llama-server router mode)."""
        response = await self.client.post(
            "/models/load",
            json={"model": model},
        )
        response.raise_for_status()
        return response.json()

    async def unload_model(self, model: str) -> dict[str, Any]:
        """POST /models/unload — unload model (llama-server router mode)."""
        response = await self.client.post(
            "/models/unload",
            json={"model": model},
        )
        response.raise_for_status()
        return response.json()

    async def health(self) -> dict[str, Any]:
        """GET /health — server health check."""
        response = await self.client.get("/health")
        response.raise_for_status()
        return response.json()
