"""
Cloud proxy client — forwards requests to the cloud proxy over loopback.

Replaces direct HTTPS to cloud providers. The proxy handles auth
injection and provider communication; this client just relays
OpenAI-format requests and SSE responses over the local network.

INVARIANT: ¬ API keys ∧ ¬ outbound HTTPS — proxy is trusted loopback
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from time import monotonic
from typing import Any

import httpx
from universal_logging import get_logger

logger = get_logger(__name__)

type HealthObserver = Callable[..., None]


def parse_cloud_proxy_url(url: str) -> tuple[str | None, str]:
    """Parse proxy URL into (uds_path, base_url).

    For unix:///path: (path, "http://localhost")
    For http://host:port: (None, full_url)
    """
    url = url.strip().rstrip("/")
    if url.startswith("unix://"):
        path = url[7:].lstrip("/")
        return (
            f"/{path}"
            if path
            else os.environ.get(
                "CLOUD_PROXY_SOCKET_PATH", "/tmp/universal-protocol/cloud-proxy.sock"
            ),
            "http://localhost",
        )
    return None, url


class CloudProxyClient:
    """HTTP client targeting the cloud proxy service over loopback.

    Supports UDS (unix://) and TCP (http://). Lifecycle:
        1. Create with ``CloudProxyClient(proxy_url)``
        2. Use ``forward_request()`` / ``forward_request_stream()``
        3. Call ``await close()`` on shutdown
    """

    def __init__(
        self,
        proxy_url: str,
        *,
        health_observer: HealthObserver | None = None,
    ) -> None:
        self._proxy_url: str = proxy_url.rstrip("/")
        self._health_observer: HealthObserver | None = health_observer
        uds_path, base_url = parse_cloud_proxy_url(proxy_url)
        timeout = httpx.Timeout(
            connect=10.0,
            read=1800.0,
            write=10.0,
            pool=10.0,
        )
        limits = httpx.Limits(max_connections=40, max_keepalive_connections=20)
        if uds_path:
            transport = httpx.AsyncHTTPTransport(uds=uds_path)
            self._client: httpx.AsyncClient = httpx.AsyncClient(
                transport=transport,
                base_url=base_url,
                timeout=timeout,
                limits=limits,
            )
        else:
            self._client = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                limits=limits,
            )

    def _observe_health(
        self,
        *,
        task: str,
        model_id: str,
        latency_ms: float,
        outcome: str,
        quality_score: float | None = None,
        tokens_per_second: float | None = None,
    ) -> None:
        if self._health_observer is None:
            return
        self._health_observer(
            task=task,
            model_id=model_id,
            latency_ms=latency_ms,
            outcome=outcome,
            quality_score=quality_score,
            tokens_per_second=tokens_per_second,
        )

    @property
    def proxy_mode(self) -> str:
        """Transport mode: 'uds' for unix://, 'tcp' for http://."""
        return "uds" if self._proxy_url.startswith("unix://") else "tcp"

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
        logger.debug("CloudProxyClient closed")

    async def forward_request(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> httpx.Response:
        """Forward a non-streaming request via the cloud proxy.

        The request body is sent as-is (OpenAI chat/completions format).
        The proxy adds auth headers and forwards to the provider.
        """
        path = "/v1/chat/completions"
        logger.debug(
            "Proxy forward (non-stream) model=%s",
            request_body.get("model", "?"),
            extra={"request_id": request_id},
        )

        start = monotonic()
        task = "general"  # task-scoping needs request-context threading (TBD)
        model_id = str(request_body.get("model", "unknown"))
        try:
            response = await self._client.post(
                path,
                json=request_body,
                headers={"Content-Type": "application/json"},
            )
        except httpx.TimeoutException:
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=(monotonic() - start) * 1000.0,
                outcome="timeout",
            )
            raise
        except httpx.RequestError:
            logger.exception(
                "Proxy request failed (non-stream)",
                extra={"request_id": request_id},
            )
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=(monotonic() - start) * 1000.0,
                outcome="error",
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected proxy failure (non-stream)",
                extra={"request_id": request_id},
            )
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=(monotonic() - start) * 1000.0,
                outcome="error",
            )
            raise

        if response.status_code >= 400:
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=(monotonic() - start) * 1000.0,
                outcome="error",
            )
            error_preview = response.text[:300]
            logger.error(
                "Proxy %d: %s",
                response.status_code,
                error_preview,
                extra={"request_id": request_id},
            )
            raise httpx.HTTPStatusError(
                f"Proxy returned {response.status_code}: {error_preview}",
                request=response.request,
                response=response,
            )

        elapsed = monotonic() - start
        tokens_per_second: float | None = None
        try:
            body = response.json()
            completion_tokens = body.get("usage", {}).get("completion_tokens")
            if (
                isinstance(completion_tokens, int | float)
                and completion_tokens > 0
                and elapsed > 0
            ):
                tokens_per_second = completion_tokens / elapsed
        except Exception:
            pass  # response body not JSON-parseable — tok/s stays None

        self._observe_health(
            task=task,
            model_id=model_id,
            latency_ms=elapsed * 1000.0,
            outcome="success",
            tokens_per_second=tokens_per_second,
        )
        return response

    async def forward_request_stream(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> AsyncIterator[bytes]:
        """Forward a streaming request via the cloud proxy.

        Yields complete SSE lines as bytes, same interface as the
        previous direct cloud forwarder for drop-in compatibility with
        FederatedRequestForwarder.
        """
        path = "/v1/chat/completions"
        body = {**request_body, "stream": True}
        logger.debug(
            "Proxy forward (stream) model=%s",
            body.get("model", "?"),
            extra={"request_id": request_id},
        )

        start = monotonic()
        task = "general"  # task-scoping needs request-context threading (TBD)
        model_id = str(body.get("model", "unknown"))
        observed_error = False
        total_content_chars = 0  # for approximate tok/s
        try:
            async with self._client.stream(
                "POST",
                path,
                json=body,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status_code >= 400:
                    observed_error = True
                    self._observe_health(
                        task=task,
                        model_id=model_id,
                        latency_ms=(monotonic() - start) * 1000.0,
                        outcome="error",
                    )
                    error_body = await response.aread()
                    error_preview = error_body.decode(errors="replace")[:300]
                    logger.error(
                        "Proxy stream %d: %s",
                        response.status_code,
                        error_preview,
                        extra={"request_id": request_id},
                    )
                    raise httpx.HTTPStatusError(
                        f"Proxy returned {response.status_code}: {error_preview}",
                        request=response.request,
                        response=response,
                    )

                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if stripped:
                        chunk = (stripped + "\n").encode("utf-8")
                        total_content_chars += len(chunk)
                        yield chunk
        except httpx.TimeoutException:
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=(monotonic() - start) * 1000.0,
                outcome="timeout",
            )
            raise
        except Exception:
            if not observed_error:
                self._observe_health(
                    task=task,
                    model_id=model_id,
                    latency_ms=(monotonic() - start) * 1000.0,
                    outcome="error",
                )
            raise
        else:
            elapsed = monotonic() - start
            approx_tokens = total_content_chars // 4  # ~4 chars/token heuristic
            tokens_per_second: float | None = (
                approx_tokens / elapsed if approx_tokens > 0 and elapsed > 0 else None
            )
            self._observe_health(
                task=task,
                model_id=model_id,
                latency_ms=elapsed * 1000.0,
                outcome="success",
                tokens_per_second=tokens_per_second,
            )

    async def forward_embedding_request(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """Forward an embedding request via the cloud proxy."""
        path = "/v1/embeddings"
        logger.debug(
            "Proxy forward (embeddings) model=%s",
            request_body.get("model", "?"),
            extra={"request_id": request_id},
        )

        response = await self._client.post(
            path, json=request_body, headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()

    async def forward_rerank_request(
        self,
        request_body: dict[str, Any],
        request_id: str,
    ) -> dict[str, Any]:
        """Forward a rerank request via the cloud proxy."""
        path = "/v1/rerank"
        logger.debug(
            "Proxy forward (rerank) model=%s",
            request_body.get("model", "?"),
            extra={"request_id": request_id},
        )

        response = await self._client.post(
            path, json=request_body, headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        return response.json()

    async def get_models(self) -> dict[str, Any]:
        """GET /api/models — full OpenRouter catalog with pricing."""
        response = await self._client.get("/api/models")
        _ = response.raise_for_status()
        return response.json()

    async def select_models(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/select — select models by capability tags and context."""
        response = await self._client.post(
            "/api/select",
            json=payload or {},
            headers={"Content-Type": "application/json"},
        )
        _ = response.raise_for_status()
        return response.json()

    async def proxy_request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Raw HTTP passthrough to the cloud proxy for UI and metadata endpoints.

        ∀ path: routed over the same transport (UDS or TCP) as inference requests.
        """
        return await self._client.request(
            method,
            path,
            content=content,
            headers=headers or {},
        )

    async def post_provider_native_json(
        self,
        path: str,
        body: dict[str, Any],
    ) -> httpx.Response:
        """POST JSON to cloud-proxy provider-native path (body preserved as-is)."""
        return await self._client.post(
            path,
            json=body,
            headers={"Content-Type": "application/json"},
        )

    async def stream_provider_native(
        self,
        path: str,
        body: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Stream POST to cloud-proxy native path (Anthropic Messages, etc.)."""
        async with self._client.stream(
            "POST",
            path,
            json=body,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_preview = error_body.decode(errors="replace")[:300]
                logger.error(
                    "Provider-native stream %d: %s",
                    response.status_code,
                    error_preview,
                )
                raise httpx.HTTPStatusError(
                    f"Proxy returned {response.status_code}: {error_preview}",
                    request=response.request,
                    response=response,
                )
            async for line in response.aiter_lines():
                stripped = line.strip()
                if stripped:
                    yield (stripped + "\n").encode()
