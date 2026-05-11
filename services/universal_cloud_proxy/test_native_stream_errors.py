"""Tests for native streaming preflight (provider errors before first chunk)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.requests import Request

from services.universal_cloud_proxy.native_streaming import preflight_native_byte_stream


def _http_status_error(*, status: int = 400) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.example.com/v1/responses")
    resp = httpx.Response(status, request=req, content=b'{"error":"bad"}')
    return httpx.HTTPStatusError("upstream failure", request=req, response=resp)


@pytest.mark.asyncio
async def test_preflight_raises_http_status_error_before_yield() -> None:
    async def source():
        raise _http_status_error()
        if False:
            yield b""

    with pytest.raises(httpx.HTTPStatusError):
        await preflight_native_byte_stream(source())


@pytest.mark.asyncio
async def test_preflight_yields_first_then_remaining() -> None:
    async def source():
        yield b"first"
        yield b"second"

    primed = await preflight_native_byte_stream(source())
    parts = [p async for p in primed]
    assert parts == [b"first", b"second"]


@pytest.mark.asyncio
async def test_preflight_empty_upstream() -> None:
    async def source():
        if False:
            yield b""

    primed = await preflight_native_byte_stream(source())
    parts = [p async for p in primed]
    assert parts == []


@pytest.mark.asyncio
async def test_forward_native_stream_surfaces_http_status_before_streaming_response() -> (
    None
):
    from fastapi import HTTPException

    from services.universal_cloud_proxy.native_routes import _forward_native

    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/providers/openai/responses",
        "raw_path": b"/api/v1/providers/openai/responses",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    request = Request(scope)

    body = {"model": "gpt-4o", "stream": True}

    class MockForwarder:
        def adapter_type(self, provider: str) -> str:
            _ = provider
            return "openai_compatible"

        def forward_native_stream(self, *, provider: str, request_body: dict):
            async def gen():
                raise _http_status_error(status=400)
                if False:
                    yield b""

            return gen()

    with patch(
        "services.universal_cloud_proxy.cloud_proxy._read_json_object_body",
        new_callable=AsyncMock,
        return_value=body,
    ):
        with pytest.raises(HTTPException) as ctx:
            await _forward_native(
                request,
                provider_key="openai",
                surface="test",
                forwarder=MockForwarder(),
                event_bus=None,
            )
    assert ctx.value.status_code == 400
    assert "Upstream provider error" in str(ctx.value.detail)


@pytest.mark.asyncio
async def test_forward_native_stream_success_returns_streaming_response() -> None:
    from starlette.responses import StreamingResponse

    from services.universal_cloud_proxy.native_routes import _forward_native

    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/providers/openai/responses",
        "raw_path": b"/api/v1/providers/openai/responses",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    request = Request(scope)
    body = {"model": "gpt-4o", "stream": True}

    class MockForwarder:
        def adapter_type(self, provider: str) -> str:
            _ = provider
            return "openai_compatible"

        def forward_native_stream(self, *, provider: str, request_body: dict):
            async def gen():
                yield b"event: x\n\n"
                yield b"event: y\n\n"

            return gen()

    with patch(
        "services.universal_cloud_proxy.cloud_proxy._read_json_object_body",
        new_callable=AsyncMock,
        return_value=body,
    ):
        resp = await _forward_native(
            request,
            provider_key="openai",
            surface="test",
            forwarder=MockForwarder(),
            event_bus=None,
        )
    assert isinstance(resp, StreamingResponse)
    streamed = [c async for c in resp.body_iterator]
    assert streamed == [b"event: x\n\n", b"event: y\n\n"]
