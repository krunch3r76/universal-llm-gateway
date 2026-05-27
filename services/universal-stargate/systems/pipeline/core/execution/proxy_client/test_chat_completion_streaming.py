"""Tests for _ProxyChatCompletionStream — SSE streaming proxy-client mixin.

These tests validate the streaming companion to chat_completion(): SSE response
consumption, request-body shaping (stream=True + stream_options.include_usage),
error semantics (4xx/5xx, non-SSE content-type, empty stream, mid-stream
network failure, stall timeout), capacity-slot lifecycle, and malformed-frame
skipping.

Mocking model: httpx.MockTransport is patched into ProxyClient via
``transport_lifecycle.make_async_client``. Streaming responses are built either
with ``content=bytes`` (one shot) or with a custom ``httpx.AsyncByteStream`` to
simulate chunked delivery and mid-stream errors.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from systems.pipeline.core.execution.proxy_client import (
    ProxyClient,
    ProxyClientConfig,
    ProxyClientError,
)
from systems.pipeline.core.execution.proxy_client import (
    chat_completion_streaming as streaming_mod,
)


def _sse_frame(payload: dict[str, Any] | str) -> bytes:
    """Render one SSE frame in the W3C ``data: ...\\n\\n`` format."""
    if isinstance(payload, dict):
        data = json.dumps(payload)
    else:
        data = payload
    return f"data: {data}\n\n".encode()


class _ErroringByteStream(httpx.AsyncByteStream):
    """AsyncByteStream that yields chunks then raises ReadError.

    Simulates a network failure that occurs mid-stream after some SSE chunks
    have already been delivered to the consumer.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        raise httpx.ReadError("simulated mid-stream failure")

    async def aclose(self) -> None:  # pragma: no cover - protocol stub
        return None


class _StallingByteStream(httpx.AsyncByteStream):
    """AsyncByteStream that yields chunks then sleeps past the stall timeout.

    Used to exercise the per-event stall-timeout path: the upstream delivers
    some chunks, then goes silent, and the consumer should fail with
    ``code="stream_stalled"`` rather than waiting for ``request_timeout``.
    """

    def __init__(self, chunks: list[bytes], sleep_seconds: float) -> None:
        self._chunks = chunks
        self._sleep = sleep_seconds

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk
        await asyncio.sleep(self._sleep)

    async def aclose(self) -> None:  # pragma: no cover - protocol stub
        return None


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    """Patch transport_utils.make_async_client to use the supplied mock transport."""
    monkeypatch.setattr(
        (
            "systems.pipeline.core.execution.proxy_client."
            "transport_lifecycle.make_async_client"
        ),
        lambda *a, **k: httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ),
    )


def _make_client() -> ProxyClient:
    return ProxyClient(
        ProxyClientConfig(stargate_url="http://localhost", request_timeout=10.0)
    )


# ---------------------------------------------------------------------------
# 1. Happy path — three content chunks + [DONE] yields three dicts in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_yields_chunks_from_sse_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [
        _sse_frame({"choices": [{"delta": {"content": "hello"}}]}),
        _sse_frame({"choices": [{"delta": {"content": " world"}}]}),
        _sse_frame(
            {
                "choices": [{"finish_reason": "stop"}],
                "usage": {"total_tokens": 5},
            }
        ),
        _sse_frame("[DONE]"),
    ]
    body = b"".join(frames)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=body,
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    chunks: list[dict[str, Any]] = []
    async for chunk in client.chat_completion_stream(
        model="m", messages=[{"role": "user", "content": "hi"}]
    ):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0]["choices"][0]["delta"]["content"] == "hello"
    assert chunks[1]["choices"][0]["delta"]["content"] == " world"
    assert chunks[2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[2]["usage"]["total_tokens"] == 5


# ---------------------------------------------------------------------------
# 2. Request body shape — method enforces stream=True + include_usage=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_sets_stream_true_and_include_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                _sse_frame({"choices": [{"delta": {"content": "x"}}]})
                + _sse_frame("[DONE]")
            ),
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    async for _chunk in client.chat_completion_stream(
        model="m", messages=[{"role": "user", "content": "x"}]
    ):
        pass

    body = captured["body"]
    assert body["stream"] is True
    assert body["stream_options"]["include_usage"] is True


# ---------------------------------------------------------------------------
# 3. Content-type mismatch — JSON when SSE expected → upstream_non_streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_raises_on_non_sse_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": []},
            headers={"Content-Type": "application/json"},
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    with pytest.raises(ProxyClientError) as exc_info:
        async for _chunk in client.chat_completion_stream(
            model="m", messages=[{"role": "user", "content": "x"}]
        ):
            pass

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("code") == "upstream_non_streaming"


# ---------------------------------------------------------------------------
# 4. Empty stream — only [DONE] sentinel, no content → empty_stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_raises_on_empty_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_sse_frame("[DONE]"),
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    with pytest.raises(ProxyClientError) as exc_info:
        async for _chunk in client.chat_completion_stream(
            model="m", messages=[{"role": "user", "content": "x"}]
        ):
            pass

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("code") == "empty_stream"


# ---------------------------------------------------------------------------
# 5. Upstream 5xx before any chunk — status_code preserved on ProxyClientError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_raises_on_upstream_5xx_before_first_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "service unavailable"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    with pytest.raises(ProxyClientError) as exc_info:
        async for _chunk in client.chat_completion_stream(
            model="m", messages=[{"role": "user", "content": "x"}]
        ):
            pass

    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# 6. Capacity-slot lifecycle on clean exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_decrements_active_requests_on_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        _sse_frame({"choices": [{"delta": {"content": "x"}}]})
        + _sse_frame("[DONE]")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=body,
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()
    starting = client._active_requests

    async for _chunk in client.chat_completion_stream(
        model="m", messages=[{"role": "user", "content": "x"}]
    ):
        pass

    assert client._active_requests == starting


# ---------------------------------------------------------------------------
# 7. Capacity-slot lifecycle on mid-stream network error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_decrements_active_requests_on_mid_stream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunks = [
        _sse_frame({"choices": [{"delta": {"content": "hello"}}]}),
        _sse_frame({"choices": [{"delta": {"content": " world"}}]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_ErroringByteStream(chunks),
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()
    starting = client._active_requests

    received: list[dict[str, Any]] = []
    with pytest.raises(ProxyClientError) as exc_info:
        async for chunk in client.chat_completion_stream(
            model="m", messages=[{"role": "user", "content": "x"}]
        ):
            received.append(chunk)

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("partial_content") is True
    assert exc_info.value.detail.get("chunks_received") == 2
    assert client._active_requests == starting
    assert len(received) == 2


# ---------------------------------------------------------------------------
# 8. Malformed-JSON frame tolerance — skip with warning, do not abort stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_skips_malformed_json_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"".join(
        [
            _sse_frame({"choices": [{"delta": {"content": "a"}}]}),
            b"data: {not valid json\n\n",
            _sse_frame({"choices": [{"delta": {"content": "b"}}]}),
            _sse_frame("[DONE]"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=body,
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    chunks: list[dict[str, Any]] = []
    async for chunk in client.chat_completion_stream(
        model="m", messages=[{"role": "user", "content": "x"}]
    ):
        chunks.append(chunk)

    assert len(chunks) == 2
    assert chunks[0]["choices"][0]["delta"]["content"] == "a"
    assert chunks[1]["choices"][0]["delta"]["content"] == "b"


# ---------------------------------------------------------------------------
# 9. Stall-timeout protection (option-c invariant) — silent upstream → fail fast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_raises_on_stream_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream falls silent mid-stream → ProxyClientError(code=stream_stalled).

    Pins the per-event stall-timeout behaviour added as part of the
    architectural choice (option (c) in the Phase 1 sidecar addendum). Without
    this, a silent backend would hang for the full request_timeout.
    """
    # Shrink the stall window so the test runs fast.
    monkeypatch.setattr(streaming_mod, "CHAT_STREAM_STALL_TIMEOUT_S", 0.1)

    chunks = [_sse_frame({"choices": [{"delta": {"content": "hello"}}]})]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_StallingByteStream(chunks, sleep_seconds=5.0),
        )

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    client = _make_client()

    received: list[dict[str, Any]] = []
    with pytest.raises(ProxyClientError) as exc_info:
        async for chunk in client.chat_completion_stream(
            model="m", messages=[{"role": "user", "content": "x"}]
        ):
            received.append(chunk)

    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("code") == "stream_stalled"
    assert len(received) == 1
