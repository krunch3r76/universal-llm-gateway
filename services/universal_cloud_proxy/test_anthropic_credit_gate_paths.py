"""A5/A10/A11 path tests — refuse-at-cap and routing guards through real gate code."""

from __future__ import annotations

import dataclasses
import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import (
    Admit,
    AnthropicCreditRefused,
    CreditPolicy,
    get_policy,
    set_policy,
)
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from model_id import ModelId
from starlette.requests import Request

from services.universal_cloud_proxy.adapters.anthropic import AnthropicAdapter
from services.universal_cloud_proxy.adapters.factory import create_provider_adapter
from services.universal_cloud_proxy.config import ProviderConfig
from services.universal_cloud_proxy.forwarder import ProviderForwarder

pytestmark = pytest.mark.offline

_CHAT_BODY = {
    "model": "anthropic/claude-sonnet-5",
    "max_tokens": 400,
    "messages": [{"role": "user", "content": "hi"}],
}
_NATIVE_BODY = {
    "model": "claude-sonnet-5",
    "max_tokens": 400,
    "messages": [{"role": "user", "content": "hi"}],
}


def _rates_path(tmp_path: Path) -> Path:
    path = tmp_path / "rates.yaml"
    path.write_text(
        """
models:
  - model_id: anthropic/claude-sonnet-5
    input_rate_per_m: 2.0
    cache_write_rate_per_m: 2.5
    cache_read_rate_per_m: 0.2
    output_rate_per_m: 10.0
    source: test
    pinned: true
""",
        encoding="utf-8",
    )
    return path


def _policy(
    tmp_path: Path,
    *,
    hard_stop_usd: Decimal,
    mcp_stop_usd: Decimal | None = None,
) -> CreditPolicy:
    base = load_credit_config()
    cfg = dataclasses.replace(
        base,
        ledger_path=tmp_path / "ledger.sqlite",
        hard_stop_usd=hard_stop_usd,
        mcp_connector_stop_usd=(
            mcp_stop_usd if mcp_stop_usd is not None else base.mcp_connector_stop_usd
        ),
        metered_models=list(
            dict.fromkeys([*base.metered_models, "anthropic/claude-sonnet-5"])
        ),
    )
    init_ledger(cfg)
    return CreditPolicy(cfg, rates_path=_rates_path(tmp_path))


def _seed_committed(policy: CreditPolicy, amount_usd: Decimal) -> None:
    output_tokens = int(amount_usd * Decimal(100_000))
    res = policy.admit(
        "seed",
        "anthropic/claude-sonnet-5",
        {
            "model": "claude-sonnet-5",
            "max_tokens": max(1, output_tokens),
            "messages": [{"role": "user", "content": "seed"}],
        },
        token_count=0,
    )
    assert isinstance(res, Admit)
    policy.commit(
        "seed",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": output_tokens,
        },
    )


def _make_adapter(
    tmp_path: Path,
    *,
    hard_stop_usd: Decimal = Decimal("0"),
    mcp_stop_usd: Decimal | None = Decimal("0"),
    committed_usd: Decimal | None = None,
    tool_use_first: bool = False,
) -> tuple[AnthropicAdapter, dict[str, int]]:
    policy = _policy(
        tmp_path, hard_stop_usd=hard_stop_usd, mcp_stop_usd=mcp_stop_usd
    )
    if committed_usd is not None:
        _seed_committed(policy, committed_usd)
    set_policy(policy)
    counts = {"messages": 0, "count_tokens": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/count_tokens"):
            counts["count_tokens"] += 1
            return httpx.Response(200, json={"input_tokens": 100})
        if path.endswith("/messages"):
            counts["messages"] += 1
            if tool_use_first and counts["messages"] == 1:
                payload = {
                    "id": "msg_tool",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "search",
                            "input": {"q": "x"},
                        }
                    ],
                    "model": "claude-sonnet-5",
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            else:
                payload = {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "model": "claude-sonnet-5",
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AnthropicAdapter(
        config=ProviderConfig(
            provider="anthropic",
            api_key="test-key",
            base_url="https://api.example.com/v1",
        ),
        client=client,
    )
    return adapter, counts


def _workbench_payload(*, max_tokens: int = 1024) -> dict[str, Any]:
    # Shape from services/mcp-server/workbench_relay.py:110-138 (non-stream MCP connector).
    return {
        "model": "claude-sonnet-5",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "workbench user"}],
        "mcp_servers": [
            {
                "type": "url",
                "url": "https://mcp.example.com/mcp/code/",
                "name": "vortex",
            }
        ],
        "tools": [{"type": "mcp_toolset", "mcp_server_name": "vortex"}],
    }


def _llm_proxy_payload() -> dict[str, Any]:
    # Body services/mcp-server/llm_proxy.py:143-159 forwards (stream popped upstream).
    return {
        "model": "anthropic/claude-sonnet-5",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "proxy user"}],
        "stream": True,
    }


def _anthropic_native_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.3", "version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/providers/anthropic/messages",
        "raw_path": b"/api/v1/providers/anthropic/messages",
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope)


async def _post_native(body: dict[str, Any], adapter: AnthropicAdapter) -> tuple[int, dict]:
    from services.universal_cloud_proxy.native_forwarders import forward_native

    forwarder = ProviderForwarder(adapters={"anthropic": adapter})
    request = _anthropic_native_request()
    with (
        patch(
            "services.universal_cloud_proxy.cloud_proxy._read_json_object_body",
            new_callable=AsyncMock,
            return_value=body,
        ),
        patch("services.universal_cloud_proxy.cloud_proxy._get_catalog", return_value=None),
    ):
        try:
            resp = await forward_native(
                request,
                provider_key="anthropic",
                surface="test",
                forwarder=forwarder,
                event_bus=None,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": exc.detail}
            return exc.status_code, detail
    assert isinstance(resp, JSONResponse)
    return resp.status_code, json.loads(resp.body)


@pytest.mark.parametrize(
    "method_name",
    [
        "forward_chat",
        "forward_chat_stream",
        "forward_native",
        "forward_native_stream",
    ],
)
@pytest.mark.asyncio
async def test_refuse_at_zero_cap_all_four_forwards(
    tmp_path: Path, method_name: str
) -> None:
    """Break covered: any forward path reaches upstream when cap is zero."""
    adapter, counts = _make_adapter(tmp_path)
    body = _CHAT_BODY if method_name.startswith("forward_chat") else _NATIVE_BODY
    with pytest.raises(AnthropicCreditRefused) as err:
        if method_name.endswith("_stream"):
            async for _chunk in getattr(adapter, method_name)(body):
                pass
        else:
            await getattr(adapter, method_name)(body)
    assert err.value.code == "anthropic_cycle_budget_exhausted"
    assert counts["messages"] == 0


@pytest.mark.asyncio
async def test_workbench_payload_refused_at_mcp_stop_after_169_99(
    tmp_path: Path,
) -> None:
    adapter, counts = _make_adapter(
        tmp_path,
        hard_stop_usd=Decimal("185"),
        mcp_stop_usd=Decimal("170"),
        committed_usd=Decimal("169.99"),
    )
    status, body = await _post_native(_workbench_payload(), adapter)
    assert status == 429
    assert body["error"]["type"] == "anthropic_cycle_budget_exhausted"
    assert counts["messages"] == 0


@pytest.mark.asyncio
async def test_workbench_payload_admitted_at_100_committed(tmp_path: Path) -> None:
    adapter, counts = _make_adapter(
        tmp_path,
        hard_stop_usd=Decimal("185"),
        mcp_stop_usd=Decimal("170"),
        committed_usd=Decimal("100"),
    )
    status, body = await _post_native(_workbench_payload(), adapter)
    assert status == 200
    assert body["id"] == "msg_test"
    assert counts["messages"] == 1
    entries = get_policy().status().get("entries") or []
    assert any(e.get("kind") == "reserve" for e in entries)


@pytest.mark.asyncio
async def test_workbench_payload_refused_at_zero_cap(tmp_path: Path) -> None:
    adapter, counts = _make_adapter(tmp_path)
    status, body = await _post_native(_workbench_payload(), adapter)
    assert status == 429
    assert body["error"]["type"] == "anthropic_cycle_budget_exhausted"
    assert counts["messages"] == 0


@pytest.mark.asyncio
async def test_llm_proxy_body_admitted_below_cap(tmp_path: Path) -> None:
    adapter, counts = _make_adapter(
        tmp_path,
        hard_stop_usd=Decimal("185"),
        mcp_stop_usd=Decimal("170"),
        committed_usd=Decimal("10"),
    )
    payload = _llm_proxy_payload()
    payload.pop("stream", None)
    status, body = await _post_native(payload, adapter)
    assert status == 200
    assert counts["messages"] == 1
    assert body["id"] == "msg_test"


@pytest.mark.asyncio
async def test_llm_proxy_body_refused_at_zero_cap(tmp_path: Path) -> None:
    adapter, counts = _make_adapter(tmp_path)
    payload = _llm_proxy_payload()
    payload.pop("stream", None)
    status, body = await _post_native(payload, adapter)
    assert status == 429
    assert body["error"]["type"] == "anthropic_cycle_budget_exhausted"
    assert counts["messages"] == 0


@pytest.mark.asyncio
async def test_native_loop_credit_cap_on_turn_two_via_real_forward_native(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break covered: native_loop turn 2 bypasses gate when send_native hits adapter directly."""
    from agent_seat import native_loop_tools as _nl_tools_mod
    from agent_seat.native_loop import run_native_tool_loop
    from llm_adapters import FrontierRequest

    adapter, counts = _make_adapter(
        tmp_path,
        hard_stop_usd=Decimal("185"),
        mcp_stop_usd=Decimal("170"),
        committed_usd=Decimal("184.992"),
        tool_use_first=True,
    )
    forwarder = ProviderForwarder(adapters={"anthropic": adapter})

    async def send_native(path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        _ = path
        return await forwarder.forward_native(
            provider="anthropic", request_body=json_body
        )

    calls = {"n": 0}

    async def _exec_tool(*_a: object, **_k: object) -> str:
        calls["n"] += 1
        return "tool-ok"

    monkeypatch.setattr(_nl_tools_mod, "execute_tool", _exec_tool)

    from llm_adapters.anthropic import AnthropicAdapter

    monkeypatch.setattr(
        "agent_seat.native_loop.resolve_llm_adapter",
        lambda *_a, **_k: AnthropicAdapter(api_key="k-test"),
    )

    req = FrontierRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="claude-sonnet-5",
        max_tokens=400,
        system="",
        mcp_tool_loop=True,
    )
    result = await run_native_tool_loop(
        model="anthropic/claude-sonnet-5",
        req=req,
        send_native=send_native,
        max_turns=5,
    )
    assert result.finish_reason == "credit_cap"
    assert result.turns_used == 2
    assert counts["messages"] == 1


@pytest.mark.asyncio
async def test_mcp_executor_run_tool_loop_one_reservation_per_iteration_refused_at_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.universal_cloud_proxy.mcp_executor import McpToolExecutor

    adapter, counts = _make_adapter(
        tmp_path,
        hard_stop_usd=Decimal("185"),
        mcp_stop_usd=Decimal("170"),
        committed_usd=Decimal("184.992"),
        tool_use_first=True,
    )

    async def forward_fn(body: dict[str, object]) -> dict[str, object]:
        return await adapter.forward_chat(dict(body))

    executor = McpToolExecutor(mcp_url="https://mcp.example/mcp")

    async def _exec_tool(*_a: object, **_k: object) -> str:
        return "tool-ok"

    monkeypatch.setattr(executor, "execute_tool", _exec_tool)
    body = {
        "model": "anthropic/claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
    }
    result = await executor.run_tool_loop(forward_fn, body, max_iterations=5)
    assert result["choices"][0]["finish_reason"] == "credit_cap"
    assert counts["messages"] == 1
    entries = get_policy().status().get("entries") or []
    reserves = [e for e in entries if e.get("kind") == "reserve"]
    assert len(reserves) >= 1


def test_openrouter_model_id_parses_openrouter_routing_layer() -> None:
    """PLAN A11: openrouter/anthropic/* bills via OpenRouter, not AnthropicAdapter."""
    parsed = ModelId.parse("openrouter/anthropic/claude-opus-5")
    assert parsed.routing_layer == "openrouter"
    assert parsed.provider == "anthropic"


def test_factory_openrouter_never_returns_anthropic_adapter() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    adapter = create_provider_adapter(
        ProviderConfig(provider="openrouter", api_key="k", base_url="https://or.example/v1"),
        client,
    )
    assert type(adapter).__name__ == "OpenAICompatibleAdapter"
    assert type(adapter).__name__ != "AnthropicAdapter"


@pytest.mark.asyncio
async def test_openrouter_chat_writes_no_ledger_row(tmp_path: Path) -> None:
    set_policy(
        _policy(tmp_path, hard_stop_usd=Decimal("185"), mcp_stop_usd=Decimal("170"))
    )
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "id": "cmpl",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = create_provider_adapter(
        ProviderConfig(provider="openrouter", api_key="k", base_url="https://or.example/v1"),
        client,
    )
    await adapter.forward_chat(
        {
            "model": "openrouter/anthropic/claude-opus-5",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 16,
        }
    )
    assert calls["n"] == 1
    entries = get_policy().status().get("entries") or []
    assert not entries


def test_resolve_pinned_rate_never_returns_openrouter_alias_row() -> None:
    from event_store.model_rate_table import resolve_pinned_rate

    assert resolve_pinned_rate("anthropic/claude-opus-5") is None
    assert resolve_pinned_rate("openrouter/anthropic/claude-opus-5") is None
