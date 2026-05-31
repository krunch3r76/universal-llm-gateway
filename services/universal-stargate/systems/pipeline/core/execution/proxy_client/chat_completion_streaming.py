"""ProxyClient chat_completion_stream operation mixin.

Streaming variant of chat_completion — opens an SSE-consuming connection to
Stargate's /v1/chat/completions endpoint and yields parsed chunk dicts as they
arrive. Used by terminal-passthrough-eligible pipelines (see
PipelineSpec.is_stream_passthrough_eligible) so the model's tokens reach the
client as they are produced instead of being buffered.

For the buffered (non-streaming) primitive every non-terminal pipeline step
uses, see chat_completion.py.

Design note — stall-timeout protection: per-event wait is bounded by
``CHAT_STREAM_STALL_TIMEOUT_S`` (default 30s) via ``asyncio.wait_for`` around
``iter_sse_events.__anext__()``. Without this, a backend that silently stalls
mid-stream would hang for the full ``request_timeout`` (3600s default). The
pattern mirrors ``libs/sse/accumulator.py``'s per-event wait loop, but without
the reducer-driven fold (passthrough yields chunks unmodified).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
from sse import SSEParseError, iter_sse_events
from sse.core import SSEMessage
from universal_logging import get_logger

from .errors import (
    ProxyClientError,
    _error_message,
    _raise_httpx_transport_error,
)

if TYPE_CHECKING:
    from .configuration import ProxyClientConfig

logger = get_logger(__name__)

# Per-event inactivity cap. The full request_timeout (default 3600s) is the
# wall-clock budget; this is the per-event "is the backend still alive" check.
# 30s is aggressive enough to fail fast on stalled upstreams, generous enough
# to tolerate brief hiccups (vLLM batch swaps, network jitter, model loading
# pauses for cold-start scenarios). Module-level constant so tests can override
# via monkeypatch without expanding the public method signature.
CHAT_STREAM_STALL_TIMEOUT_S: float = 30.0


class _ProxyChatCompletionStream:
    """Mixin providing the chat_completion_stream coroutine for ProxyClient."""

    _config: ProxyClientConfig
    _active_requests: int

    async def chat_completion_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        execution_id: str | None = None,
        step_id: str | None = None,
        skip_token_counting: bool = False,
        disable_profile: bool = True,
        profile: str | None = None,
        timeout: float | None = None,
        map_iteration_request_id: str | None = None,
        request_id: str | None = None,
        **params: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Open an SSE stream to /v1/chat/completions and yield chunks.

        Args identical to chat_completion(); see that method for documentation.
        ``stream`` and ``stream_options.include_usage`` are set by this method
        regardless of caller params (mirror of chat_completion's stream=False
        override). Any caller-supplied ``stream_options`` are merged with
        ``include_usage=True`` taking precedence on that one key.

        Yields:
            Each parsed SSE chunk's JSON payload as a dict. Chunks arrive in
            upstream order. The final chunk carries ``usage`` when the inference
            backend supports ``stream_options.include_usage`` (vLLM, llama.cpp's
            OpenAI shim, OpenRouter, OpenAI, Anthropic all do). When the backend
            does not, the final chunk omits ``usage`` and the consumer handles
            that gracefully.

        Raises:
            ProxyClientError: on upstream HTTP error before first chunk; on
              non-SSE response despite stream=True (``code="upstream_non_streaming"``);
              on empty stream (``code="empty_stream"``); on network error mid-stream
              (``detail.partial_content=True``); on stall (``code="stream_stalled"``);
              on malformed SSE framing (``code="malformed_sse_framing"``); on
              upstream-emitted error event (``code="upstream_stream_error"``).
        """
        client = await self._ensure_client()

        if map_iteration_request_id is None:
            map_iteration_request_id = str(uuid.uuid4())
        # See chat_completion.py for the per-call capacity-slot rationale.
        unique_request_id = request_id or str(uuid.uuid4())

        # stream=True + stream_options.include_usage=True are method-enforced
        # invariants (mirror of buffered method's stream=False override). Merge
        # any caller-provided stream_options so future OpenAI additions don't
        # silently break callers.
        request_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **params,
        }
        request_body["stream"] = True
        existing_stream_options = request_body.get("stream_options") or {}
        request_body["stream_options"] = {
            **existing_stream_options,
            "include_usage": True,
        }

        request_headers = self._build_request_headers(
            execution_id,
            step_id,
            skip_token_counting,
            timeout,
            request_id=unique_request_id,
            cancel_group=map_iteration_request_id,
        )

        # Identical to chat_completion's profile-control query-param logic.
        query_params: dict[str, str] = {}
        if disable_profile and not profile:
            query_params["disable_profile"] = "true"
        if profile:
            query_params["filter"] = profile

        request_timeout = timeout or self._config.request_timeout

        self._active_requests += 1
        chunks_yielded = 0
        try:
            try:
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json=request_body,
                    headers=request_headers,
                    params=query_params,
                    timeout=request_timeout,
                ) as response:
                    if response.status_code >= 400:
                        await self._raise_pre_stream_http_error(response)

                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type.lower():
                        body_preview = (await response.aread()).decode(
                            "utf-8", errors="replace"
                        )[:500]
                        raise ProxyClientError(
                            (
                                "Expected text/event-stream from "
                                f"/v1/chat/completions, got {content_type!r}"
                            ),
                            status_code=response.status_code,
                            detail={
                                "code": "upstream_non_streaming",
                                "content_type": content_type,
                                "body_preview": body_preview,
                            },
                        )

                    events_iter = iter_sse_events(response.aiter_bytes())
                    try:
                        async for chunk in self._iter_stream_chunks(events_iter):
                            chunks_yielded += 1
                            yield chunk
                    finally:
                        await events_iter.aclose()

                    if chunks_yielded == 0:
                        raise ProxyClientError(
                            "SSE stream produced no chunks before [DONE]",
                            status_code=response.status_code,
                            detail={"code": "empty_stream"},
                        )
            except httpx.TimeoutException as e:
                await self._raise_timeout_error(
                    endpoint="/v1/chat/completions",
                    request_timeout=request_timeout,
                    request_body=request_body,
                    request_headers=request_headers,
                    execution_id=execution_id,
                    step_id=step_id,
                    exception=e,
                    request_kind="chat_stream",
                )
            except ProxyClientError:
                # Already shaped; preserve detail/status_code as-is.
                raise
            except SSEParseError as e:
                raise ProxyClientError(
                    f"Malformed SSE framing from upstream: {e}",
                    detail={
                        "code": "malformed_sse_framing",
                        "partial_content": chunks_yielded > 0,
                        "chunks_received": chunks_yielded,
                        "error": str(e),
                    },
                ) from e
            except httpx.HTTPError as e:
                if chunks_yielded > 0:
                    raise ProxyClientError(
                        (
                            "Network error mid-stream after "
                            f"{chunks_yielded} chunks: {e}"
                        ),
                        detail={
                            "code": "mid_stream_network_error",
                            "partial_content": True,
                            "chunks_received": chunks_yielded,
                            "error": str(e),
                        },
                    ) from e
                _raise_httpx_transport_error(e)
        finally:
            self._active_requests -= 1

    @staticmethod
    async def _raise_pre_stream_http_error(response: httpx.Response) -> None:
        """Read the (non-2xx, pre-stream) body and raise ProxyClientError.

        Mirrors chat_completion's 4xx/5xx handling: parse upstream detail when
        available, fall back to raw text. Async because the body must be
        explicitly read on a streaming response.
        """
        raw = await response.aread()
        text = raw.decode("utf-8", errors="replace") if raw else ""
        detail: Any
        try:
            error_body = json.loads(text) if text else {}
            detail = (
                error_body.get("detail", error_body)
                if isinstance(error_body, dict)
                else text
            )
        except json.JSONDecodeError:
            detail = text
        raise ProxyClientError(
            _error_message(response.status_code, detail),
            status_code=response.status_code,
            detail=detail,
        )

    @staticmethod
    async def _iter_stream_chunks(
        events: AsyncIterator[SSEMessage],
    ) -> AsyncIterator[dict[str, Any]]:
        """Drive the SSE event iterator with stall-timeout protection.

        Yields parsed chunk dicts. Stops on ``data: [DONE]``. Skips malformed-
        JSON frames with a warning. Raises ProxyClientError on stall or on
        upstream-emitted SSE error event. Other exceptions (SSEParseError,
        httpx.HTTPError) propagate to the caller for shape-specific handling.
        """
        while True:
            try:
                event = await asyncio.wait_for(
                    events.__anext__(),
                    timeout=CHAT_STREAM_STALL_TIMEOUT_S,
                )
            except TimeoutError as e:
                raise ProxyClientError(
                    (
                        "SSE stream stalled — no event in "
                        f"{CHAT_STREAM_STALL_TIMEOUT_S}s"
                    ),
                    detail={
                        "code": "stream_stalled",
                        "stall_timeout_seconds": CHAT_STREAM_STALL_TIMEOUT_S,
                    },
                ) from e
            except StopAsyncIteration:
                return

            if event.event == "error":
                raise ProxyClientError(
                    "Upstream emitted SSE error event",
                    detail={
                        "code": "upstream_stream_error",
                        "data": event.data,
                    },
                )

            data_str = event.data if isinstance(event.data, str) else ""
            if data_str == "[DONE]":
                return

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning(
                    "chat_completion_stream: skipping malformed-JSON SSE "
                    "frame (len=%d)",
                    len(data_str),
                )
                continue

            if not isinstance(chunk, dict):
                logger.warning(
                    "chat_completion_stream: skipping non-dict SSE payload (type=%s)",
                    type(chunk).__name__,
                )
                continue

            yield chunk
