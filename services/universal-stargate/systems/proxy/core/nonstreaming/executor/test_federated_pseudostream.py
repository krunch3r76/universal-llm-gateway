"""Unit tests for federated pseudostream accumulation."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from systems.proxy.core.nonstreaming.executor.federated_pseudostream import (
    PSEUDOSTREAM_HEADER,
    PSEUDOSTREAM_HEADER_VALUE,
    PSEUDOSTREAM_UPSTREAM_STREAM_HEADER,
    _execute_federated_pseudostream,
)


async def _sse_bytes() -> AsyncIterator[bytes]:
    chunks = [
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": "hermes-3-test",
            "choices": [{"index": 0, "delta": {"content": "Hi"}}],
        },
        {
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": "!"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    ]
    for payload in chunks:
        yield f"data: {json.dumps(payload)}\n\n".encode()
    yield b"data: [DONE]\n\n"


class _FakeStreamingResponse:
    def __init__(self) -> None:
        self.body_iterator = _sse_bytes()


@pytest.mark.asyncio
async def test_pseudostream_accumulates_to_json() -> None:
    context = SimpleNamespace(
        selected_model="hermes-3-test",
        request_timeout_hint=None,
    )
    release = AsyncMock()

    with patch(
        "systems.proxy.core.nonstreaming.executor.federated_pseudostream._execute_federated_streaming",
        new=AsyncMock(return_value=_FakeStreamingResponse()),
    ), patch(
        "systems.proxy.core.nonstreaming.executor.federated_pseudostream.write_request_snapshot",
        new=AsyncMock(),
    ):
        response = await _execute_federated_pseudostream(
            context,  # type: ignore[arg-type]
            fed_gateway=SimpleNamespace(gateway_id="edge-test"),
            request_body={"model": "hermes-3-test", "stream": False},
            request_id="req-pseudo-1",
            hop_count=1,
            endpoint_category=SimpleNamespace(),
            hints=None,
            federation_integration=None,
            federation_forwarder=None,
            release_capacity_token=release,
        )

    assert response.headers[PSEUDOSTREAM_HEADER] == PSEUDOSTREAM_HEADER_VALUE
    assert response.headers[PSEUDOSTREAM_UPSTREAM_STREAM_HEADER] == "true"
    assert response.headers["X-ULG-Pseudostream-Delta-Parts"] == "2"
    assert response.headers["X-ULG-Pseudostream-Sse-Events"] == "2"
    assert "chat.completion.chunk" in response.headers["X-ULG-Pseudostream-Chunk-Objects"]
    assert response.headers["X-ULG-Pseudostream-Saw-Done"] == "1"
    body: dict[str, Any] = json.loads(response.body)
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hi!"
    assert body["usage"]["total_tokens"] == 3


@pytest.mark.asyncio
async def test_pseudostream_empty_error_raises() -> None:
    async def _err_bytes() -> AsyncIterator[bytes]:
        err = {
            "error": {
                "message": "empty stream",
                "code": "empty_stream",
            }
        }
        yield f"data: {json.dumps(err)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    class _ErrResp:
        def __init__(self) -> None:
            self.body_iterator = _err_bytes()

    context = SimpleNamespace(
        selected_model="hermes-3-test",
        request_timeout_hint=None,
    )

    with patch(
        "systems.proxy.core.nonstreaming.executor.federated_pseudostream._execute_federated_streaming",
        new=AsyncMock(return_value=_ErrResp()),
    ), patch(
        "systems.proxy.core.nonstreaming.executor.federated_pseudostream.write_request_snapshot",
        new=AsyncMock(),
    ):
        with pytest.raises(HTTPException) as exc:
            await _execute_federated_pseudostream(
                context,  # type: ignore[arg-type]
                fed_gateway=SimpleNamespace(gateway_id="edge-test"),
                request_body={"model": "hermes-3-test"},
                request_id="req-pseudo-2",
                hop_count=1,
                endpoint_category=SimpleNamespace(),
                hints=None,
                federation_integration=None,
                federation_forwarder=None,
                release_capacity_token=AsyncMock(),
            )
    assert exc.value.status_code == 502
