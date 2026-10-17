"""Tests for actual usage cost calculation (leg A9 core)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from event_store.model_rate_table import ModelRateRow

from anthropic_credit.usage_cost import usage_cost_usd

pytestmark = pytest.mark.offline


def _sonnet_row() -> ModelRateRow:
    return ModelRateRow(
        model_id="anthropic/claude-sonnet-5",
        input_rate_per_m=2.0,
        output_rate_per_m=10.0,
        cache_write_rate_per_m=2.5,
        cache_read_rate_per_m=0.2,
        source="manual_seed",
        updated_at="2026-10-17T00:00:00+00:00",
        pinned=True,
    )


def _haiku_long_row() -> ModelRateRow:
    return ModelRateRow(
        model_id="anthropic/claude-haiku-5-5",
        input_rate_per_m=0.10,
        output_rate_per_m=0.50,
        cache_write_rate_per_m=0.125,
        cache_read_rate_per_m=0.01,
        long_prompt_threshold_tokens=100_000,
        long_input_rate_per_m=0.50,
        long_cache_write_rate_per_m=0.625,
        long_cache_read_rate_per_m=0.05,
        long_output_rate_per_m=2.50,
        source="manual_seed",
        updated_at="2026-10-17T00:00:00+00:00",
        pinned=True,
    )


def test_exact_usd_with_5m_and_1h_breakdown() -> None:
    """Break covered: dropping cache_creation_input_tokens or mispricing 5m vs 1h tiers."""
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 248,
        "cache_read_input_tokens": 2000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 148,
            "ephemeral_1h_input_tokens": 100,
        },
        "output_tokens": 400,
    }
    cost, missing, _unknown = usage_cost_usd(usage, _sonnet_row())
    assert missing == ()
    # input: 1000 * 2.0 / 1e6 = 0.002000
    # 5m write: 148 * 2.5 / 1e6 = 0.000370
    # 1h write: 100 * 4.0 / 1e6 = 0.000400
    # cache read: 2000 * 0.2 / 1e6 = 0.000400
    # output: 400 * 10.0 / 1e6 = 0.004000
    # sum = 0.007170
    assert cost == Decimal("0.00717")


def test_no_breakdown_prices_cache_creation_at_2x_input() -> None:
    """Break covered: missing cache_creation breakdown underpricing 1h writes at 1.25x."""
    usage = {
        "input_tokens": 1000,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": 0,
        "output_tokens": 100,
    }
    cost, missing, _unknown = usage_cost_usd(usage, _sonnet_row())
    assert missing == ()
    # input: 1000 * 2.0 / 1e6 = 0.002
    # cache_creation (2x input = 4.0): 500 * 4.0 / 1e6 = 0.002
    # output: 100 * 10.0 / 1e6 = 0.001
    assert cost == Decimal("0.005")


def test_long_tier_triggered_by_combined_input_and_cache_tokens() -> None:
    """Break covered: long-tier check ignoring cache_read/cache_creation in total input."""
    usage = {
        "input_tokens": 1_000,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 120_000,
        "output_tokens": 2_000,
    }
    cost, missing, _unknown = usage_cost_usd(usage, _haiku_long_row())
    assert missing == ()
    # Total input = 121,000 > 100,000 -> long tier:
    # input: 1,000 * 0.50 / 1e6 = 0.0005
    # cache_read: 120,000 * 0.05 / 1e6 = 0.0060
    # output: 2,000 * 2.50 / 1e6 = 0.0050
    # total = 0.0115
    assert cost == Decimal("0.0115")


def test_missing_counters_listed_for_none_and_partial_usage() -> None:
    """Break covered: partial/missing usage silently treated as 0 tokens without flagging missing fields."""
    row = _sonnet_row()

    cost_none, missing_none, _u0 = usage_cost_usd(None, row)
    assert cost_none is None
    assert missing_none == (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )

    cost_empty, missing_empty, _u1 = usage_cost_usd({}, row)
    assert cost_empty is None
    assert len(missing_empty) == 4

    # OpenAI-translated or stream-truncated usage missing cache fields and/or output_tokens
    partial = {"input_tokens": 1000, "output_tokens": 200}
    cost_partial, missing_partial, _u2 = usage_cost_usd(partial, row)
    assert missing_partial == (
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    # 1000 * 2.0 / 1e6 + 200 * 10.0 / 1e6 = 0.004
    assert cost_partial == Decimal("0.004")
