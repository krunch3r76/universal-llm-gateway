"""ProxyClient embeddings and rerank operation mixins.

Non-chat vector model invocations that reuse the same Stargate proxy
transport, header, timeout, and error conventions as chat_completion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from .errors import (
    ProxyClientError,
    _error_message,
    _raise_httpx_transport_error,
)

if TYPE_CHECKING:
    from .configuration import ProxyClientConfig


class _ProxyVectorRequests:
    """Mixin providing embeddings() and rerank() for ProxyClient."""

    _config: ProxyClientConfig
    _active_requests: int

    async def embeddings(
        self,
        model: str,
        texts: list[str],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute embedding request via Stargate.

        Routes through Stargate's full pipeline:
        - Gateway selection and routing
        - Capacity tracking (via orchestrator)
        - Federation forwarding

        Args:
            model: Embedding model identifier
            texts: Texts to embed
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            timeout: Request timeout (overrides default)

        Returns:
            OpenAI-compatible embedding response dict:
            {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [...], "index": 0}, ...],
                "model": "...",
                "usage": {"prompt_tokens": N, "total_tokens": N}
            }

        Raises:
            ProxyClientError: On request failure (includes status_code)
        """
        client = await self._ensure_client()

        # Build request body
        request_body: dict[str, Any] = {
            "model": model,
            "input": texts,
        }

        # Build headers for internal identification
        request_headers = self._build_request_headers(
            execution_id, step_id, skip_token_counting=True, timeout=timeout
        )

        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/v1/embeddings",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json()

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/v1/embeddings",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="embedding",
            )
        except httpx.HTTPError as e:
            _raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1

    async def rerank(
        self,
        model: str,
        query: str,
        passages: list[str],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute rerank request via Stargate.

        Routes through Stargate's full pipeline:
        - Gateway selection and routing
        - Capacity tracking (via orchestrator)
        - Federation forwarding

        Args:
            model: Reranker model identifier
            query: Query to score against passages
            passages: Passages to score
            execution_id: Pipeline execution ID (for tracing)
            step_id: Pipeline step ID (for tracing)
            timeout: Request timeout (overrides default)

        Returns:
            Rerank response dict:
            {
                "scores": [0.91, 0.42, ...],
                "model": "baai-bge-reranker-v2-m3"
            }

        Raises:
            ProxyClientError: On request failure (includes status_code)
        """
        client = await self._ensure_client()

        request_body: dict[str, Any] = {
            "model": model,
            "query": query,
            "passages": passages,
        }

        request_headers = self._build_request_headers(
            execution_id, step_id, skip_token_counting=True, timeout=timeout
        )

        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        try:
            response = await client.post(
                "/api/v1/rerank",
                json=request_body,
                headers=request_headers,
                timeout=request_timeout,
            )

            if response.status_code >= 400:
                try:
                    error_body = response.json()
                    detail = error_body.get("detail", error_body)
                except Exception:
                    detail = response.text

                raise ProxyClientError(
                    _error_message(response.status_code, detail),
                    status_code=response.status_code,
                    detail=detail,
                )

            return response.json()

        except httpx.TimeoutException as e:
            await self._raise_timeout_error(
                endpoint="/api/v1/rerank",
                request_timeout=request_timeout,
                request_body=request_body,
                request_headers=request_headers,
                execution_id=execution_id,
                step_id=step_id,
                exception=e,
                request_kind="rerank",
            )
        except httpx.HTTPError as e:
            _raise_httpx_transport_error(e)

        finally:
            self._active_requests -= 1
