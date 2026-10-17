"""R8 slice 4 — StargateProxy header parsing (timeout + embedding class)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from systems.proxy.stargate.proxy import StargateProxy


def _proxy_with_capture() -> tuple[StargateProxy, dict]:
    proxy = object.__new__(StargateProxy)
    proxy.config = MagicMock()
    proxy.config.get_embedding_failover_config.return_value = {
        "query_cpu_failover": True,
        "batch_cpu_failover": True,
    }
    captured: dict = {}
    proxy.request_executor = MagicMock()

    async def _exec(**kwargs):
        captured.update(kwargs)
        return {"data": []}

    proxy.request_executor.execute_embedding_request = AsyncMock(side_effect=_exec)
    return proxy, captured


def _request(headers: dict) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(request_id="req-h"), headers=headers)


@pytest.mark.asyncio
async def test_proxy_parses_float_x_request_timeout() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Request-Timeout": "1.25"}),
    )
    assert captured["request_timeout_hint"] == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_proxy_parses_subsecond_x_request_timeout() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Request-Timeout": "0.5"}),
    )
    assert captured["request_timeout_hint"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_proxy_absent_x_request_timeout_is_none() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({}),
    )
    assert captured["request_timeout_hint"] is None


@pytest.mark.asyncio
async def test_proxy_non_positive_x_request_timeout_ignored() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Request-Timeout": "0"}),
    )
    assert captured["request_timeout_hint"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("header_value", ["-1", "-0.5"])
async def test_proxy_negative_x_request_timeout_ignored(header_value: str) -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Request-Timeout": header_value}),
    )
    assert captured["request_timeout_hint"] is None


@pytest.mark.asyncio
async def test_proxy_garbage_x_request_timeout_ignored() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Request-Timeout": "not-a-number"}),
    )
    assert captured["request_timeout_hint"] is None


@pytest.mark.asyncio
async def test_proxy_x_embedding_class_query_vs_batch() -> None:
    proxy, captured = _proxy_with_capture()
    await proxy.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Embedding-Class": "query"}),
    )
    assert captured["query_cpu_failover_enabled"] is True
    assert captured["batch_cpu_failover_enabled"] is False

    proxy2, captured2 = _proxy_with_capture()
    await proxy2.process_embedding_request(
        "qwen3-embedding-8b-q8-0-8192",
        ["text"],
        _request({"X-Embedding-Class": "batch"}),
    )
    assert captured2["query_cpu_failover_enabled"] is False
    assert captured2["batch_cpu_failover_enabled"] is True
