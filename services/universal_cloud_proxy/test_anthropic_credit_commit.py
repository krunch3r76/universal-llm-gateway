"""A9 commit-path tests — raw Anthropic usage before OpenAI translation."""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy, get_policy, set_policy

from services.universal_cloud_proxy.adapters.anthropic import AnthropicAdapter
from services.universal_cloud_proxy.config import ProviderConfig

pytestmark = pytest.mark.offline

_FIXTURES = Path(__file__).parent / "fixtures" / "anthropic_usage"


def _load_json(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _load_sse(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _make_adapter(
    upstream_payload: dict | str,
    *,
    stream: bool = False,
) -> tuple[AnthropicAdapter, dict[str, int]]:
    counts = {"messages": 0, "count_tokens": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/count_tokens"):
            counts["count_tokens"] += 1
            return httpx.Response(200, json={"input_tokens": 1000})
        if path.endswith("/messages"):
            counts["messages"] += 1
            if stream:
                sse = upstream_payload if isinstance(upstream_payload, str) else ""
                return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
            assert isinstance(upstream_payload, dict)
            return httpx.Response(200, json=upstream_payload)
        return httpx.Response(404, text="not found")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = ProviderConfig(
        provider="anthropic",
        api_key="test-key",
        base_url="https://api.example.com/v1",
    )
    return AnthropicAdapter(config=config, client=client), counts


def _committed_for_last_request() -> Decimal:
    st = get_policy().status()
    entries = st.get("entries") or st.get("last_entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    assert commits, "expected a commit ledger entry"
    return Decimal(str(commits[-1]["usd"]))


@pytest.mark.asyncio
async def test_non_stream_full_breakdown_exact_usd() -> None:
    payload = _load_json("non_stream_full_breakdown.json")
    adapter, _counts = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_for_last_request() == Decimal("0.00717")


@pytest.mark.asyncio
async def test_non_stream_no_breakdown_prices_cache_creation_at_2x() -> None:
    payload = _load_json("non_stream_no_breakdown.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_for_last_request() == Decimal("0.005")


@pytest.mark.asyncio
async def test_stream_message_start_and_delta_exact_usd() -> None:
    sse = _load_sse("stream_full_usage.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    chunks: list[bytes] = []
    async for chunk in adapter.forward_native_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        chunks.append(chunk)
    assert chunks
    assert _committed_for_last_request() == Decimal("0.00717")


@pytest.mark.asyncio
async def test_stream_delta_overrides_start_usage_no_double_count() -> None:
    sse = _load_sse("stream_delta_overrides_input.sse")
    adapter, _ = _make_adapter(sse, stream=True)
    async for _chunk in adapter.forward_native_stream(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    ):
        pass
    assert _committed_for_last_request() == Decimal("0.00737")


@pytest.mark.asyncio
async def test_stream_truncated_before_delta_commits_full_reservation() -> None:
    """Break covered: stream ends without message_delta usage → commit_full reservation."""
    from anthropic_credit.policy import Admit, admit

    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        StreamUsageTap,
        finalize_stream_credit,
    )

    request_id = "stream-truncated-req"
    upstream_body = {
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    reservation = admit(
        request_id,
        "anthropic/claude-sonnet-5",
        upstream_body,
        token_count=1000,
    )
    assert isinstance(reservation, Admit)

    tap = StreamUsageTap()
    for line in _load_sse("stream_truncated_before_delta.sse").splitlines():
        tap.feed_line(line)

    assert tap.delta_usage_seen is False
    finalize_stream_credit(request_id, tap, normal_end=True)

    st = get_policy().status()
    entries = st.get("entries") or []
    commits = [e for e in entries if e.get("kind") == "commit"]
    assert commits, f"expected commit entry, got {entries!r}"
    last = commits[-1]
    assert last["kind"] == "commit"
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    assert Decimal(str(last["usd"])) == Decimal(str(reserve["usd"]))


@pytest.mark.asyncio
async def test_non_stream_missing_usage_commits_full_reservation() -> None:
    payload = _load_json("non_stream_missing_usage.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-sonnet-5",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    st = get_policy().status()
    entries = st.get("entries") or []
    reserve = next(e for e in entries if e.get("kind") == "reserve")
    commit = next(e for e in entries if e.get("kind") == "commit")
    assert Decimal(str(commit["usd"])) == Decimal(str(reserve["usd"]))


@pytest.mark.asyncio
async def test_haiku_long_tier_cache_read_only(tmp_path: Path) -> None:
    rates_path = tmp_path / "rates.yaml"
    rates_path.write_text(
        """
models:
  - model_id: anthropic/claude-haiku-5-5
    input_rate_per_m: 0.10
    output_rate_per_m: 0.50
    cache_write_rate_per_m: 0.125
    cache_read_rate_per_m: 0.01
    long_prompt_threshold_tokens: 100000
    long_input_rate_per_m: 0.50
    long_cache_write_rate_per_m: 0.625
    long_cache_read_rate_per_m: 0.05
    long_output_rate_per_m: 2.50
    source: test
    pinned: true
""",
        encoding="utf-8",
    )
    base = load_credit_config()
    cfg = dataclasses.replace(
        base,
        ledger_path=tmp_path / "ledger.sqlite",
        metered_models=[
            *base.metered_models,
            "anthropic/claude-haiku-5-5",
        ],
    )
    init_ledger(cfg)
    set_policy(CreditPolicy(cfg, rates_path=rates_path))

    payload = _load_json("non_stream_haiku_long_tier.json")
    adapter, _ = _make_adapter(payload)
    await adapter.forward_native(
        {
            "model": "claude-haiku-5-5",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert _committed_for_last_request() == Decimal("0.0115")


@pytest.mark.asyncio
async def test_openai_shape_byte_identical_after_gate() -> None:
    payload = _load_json("non_stream_full_breakdown.json")
    adapter, counts = _make_adapter(payload)
    request_body = {
        "model": "anthropic/claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    gated = await adapter.forward_chat(request_body)
    direct = adapter._anthropic_to_openai_response(payload, request_body["model"])[0]
    assert json.dumps(gated, sort_keys=True) == json.dumps(direct, sort_keys=True)
    assert counts["messages"] == 1
