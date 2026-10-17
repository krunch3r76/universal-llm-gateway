"""A4 route and refusal tests for the Anthropic credit gate."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import (
    Admit,
    AnthropicCreditRefused,
    CreditPolicy,
    set_policy,
)
from fastapi.testclient import TestClient

from services.universal_cloud_proxy.adapters.anthropic import AnthropicAdapter
from services.universal_cloud_proxy.config import ProviderConfig

pytestmark = pytest.mark.offline


def _tight_policy(tmp_path: Path, committed_usd: str) -> CreditPolicy:
    base = load_credit_config()
    cfg = dataclasses.replace(
        base,
        ledger_path=tmp_path / "tight.sqlite",
        hard_stop_usd=Decimal("185"),
        mcp_connector_stop_usd=Decimal("170"),
    )
    init_ledger(cfg)
    policy = CreditPolicy(cfg)
    _seed = policy.admit(
        "seed",
        "anthropic/claude-sonnet-5",
        {
            "model": "claude-sonnet-5",
            "max_tokens": 100_000,
            "messages": [{"role": "user", "content": "x"}],
        },
        token_count=0,
    )
    assert isinstance(_seed, Admit)
    policy.commit(
        "seed",
        {
            "input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": int(Decimal(committed_usd) * 100_000),
        },
    )
    return policy


@pytest.mark.asyncio
async def test_refusal_makes_zero_messages_calls(tmp_path: Path) -> None:
    set_policy(_tight_policy(tmp_path, "184.00"))
    counts = {"messages": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            counts["messages"] += 1
        return httpx.Response(200, json={"usage": {"input_tokens": 1, "output_tokens": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = AnthropicAdapter(
        config=ProviderConfig(
            provider="anthropic",
            api_key="k",
            base_url="https://api.example.com/v1",
        ),
        client=client,
    )
    with pytest.raises(AnthropicCreditRefused) as err:
        await adapter.forward_native(
            {
                "model": "claude-sonnet-5",
                "max_tokens": 100_000,
                "messages": [{"role": "user", "content": "x"}],
            }
        )
    assert err.value.code == "anthropic_cycle_budget_exhausted"
    assert counts["messages"] == 0


def test_internal_status_route_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.universal_cloud_proxy import cloud_proxy as cp

    class _Catalog:
        def get_all_models(self) -> list[str]:
            return []

    monkeypatch.setattr(cp, "_get_catalog", lambda: _Catalog())
    monkeypatch.setattr(cp, "_get_forwarder", lambda: object())
    monkeypatch.setattr(cp, "_get_event_bus", lambda: None)

    with TestClient(cp.app) as client:
        resp = client.get("/internal/anthropic-credit/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "healthy" in body
    assert "headroom_usd" in body
    assert "window_start" in body
