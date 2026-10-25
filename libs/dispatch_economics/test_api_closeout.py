"""Tests for API-role closeout economics footer."""

from __future__ import annotations

import json

import pytest

from dispatch_economics.api_closeout import append_api_closeout_economics, price_usage
from event_store.model_rate_table import ModelRateRow


@pytest.mark.offline
def test_price_usage_maps_tokens() -> None:
    priced = price_usage(
        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        model="anthropic/claude-sonnet-4-6",
    )
    assert priced is not None
    assert priced["input_tokens"] == 10
    assert priced["output_tokens"] == 5
    assert priced.get("cost_source") in (
        "rate_x_tokens",
        "yaml",
        "wire",
        "unavailable",
    )


@pytest.mark.offline
def test_price_usage_anthropic_exclusive_sum_and_cache_write() -> None:
    priced = price_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_tokens": 40,
            "cache_creation_input_tokens": 10,
        },
        model="anthropic/claude-sonnet-4-6",
    )
    assert priced is not None
    assert priced.get("cache_read_tokens") == 40
    assert priced.get("cache_write_tokens") == 10
    assert priced.get("cost_source") in (
        "rate_x_tokens",
        "yaml",
        "wire",
        "unavailable",
    )


@pytest.mark.offline
def test_price_usage_anthropic_exclusive_sum_rate_x_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_rate = ModelRateRow(
        model_id="anthropic/claude-sonnet-4-6",
        input_rate_per_m=3.0,
        output_rate_per_m=15.0,
        source="manual_seed_local",
        updated_at="2026-01-01T00:00:00+00:00",
        cache_read_rate_per_m=0.3,
        cache_write_rate_per_m=3.75,
    )
    monkeypatch.setattr(
        "event_store.dispatch_economics_pricing.resolve_rate",
        lambda *_a, **_k: fixed_rate,
    )
    priced = price_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 20,
            "cached_tokens": 2000,
            "cache_write_tokens": 248,
        },
        model="anthropic/claude-sonnet-4-6",
    )
    assert priced is not None
    assert priced["cost_source"] == "rate_x_tokens"
    expected = (
        (1000 / 1_000_000) * 3.0
        + (2000 / 1_000_000) * 0.3
        + (248 / 1_000_000) * 3.75
        + (20 / 1_000_000) * 15.0
    )
    assert priced["cost_usd"] == pytest.approx(round(expected, 8))


@pytest.mark.offline
def test_append_api_closeout_includes_work_key_and_json() -> None:
    body = append_api_closeout_economics(
        "closeout text",
        work_key="todo:ac",
        execution_id="exec-1",
        model="anthropic/claude-sonnet-4-6",
        usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        contract="freeform",
    )
    assert "work_key: todo:ac" in body
    assert "## dispatch economics" in body
    assert "```json" in body
    block = body.split("```json")[1].split("```")[0].strip()
    parsed = json.loads(block)
    assert parsed["work_key"] == "todo:ac"
    assert parsed["execution_id"] == "exec-1"
