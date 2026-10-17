"""Leg B1: pinned rate rows and credit-gate admission for sonnet/haiku 5-5."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from event_store.model_rate_table import resolve_pinned_rate, resolve_rate

from anthropic_credit.config import load_credit_config
from anthropic_credit.policy import Admit, CreditPolicy
from anthropic_credit.usage_cost import usage_cost_usd

pytestmark = pytest.mark.offline

_T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


def test_resolve_rate_sonnet_5_5_exact() -> None:
    row = resolve_rate("anthropic/claude-sonnet-5-5")
    assert row is not None
    assert row.pinned is True
    assert row.input_rate_per_m == 2.0
    assert row.cache_write_rate_per_m == 2.5
    assert row.cache_read_rate_per_m == 0.10
    assert row.output_rate_per_m == 10.0
    assert "platform pricing page 2026-10-16" in row.source


def test_resolve_rate_haiku_5_5_standard_tier() -> None:
    row = resolve_rate("anthropic/claude-haiku-5-5")
    assert row is not None
    assert row.input_rate_per_m == 0.10
    assert row.cache_write_rate_per_m == 0.125
    assert row.cache_read_rate_per_m == 0.01
    assert row.output_rate_per_m == 0.50
    assert row.long_prompt_threshold_tokens == 100_000
    assert row.long_input_rate_per_m == 0.50
    assert row.long_cache_write_rate_per_m == 0.625
    assert row.long_cache_read_rate_per_m == 0.05
    assert row.long_output_rate_per_m == 2.50


@pytest.mark.parametrize(
    ("prompt_tokens", "input_rate"),
    [
        (99_999, 0.10),
        (100_001, 0.50),
    ],
)
def test_haiku_5_5_long_tier_boundary(prompt_tokens: int, input_rate: float) -> None:
    row = resolve_pinned_rate("anthropic/claude-haiku-5-5")
    assert row is not None
    usage = {
        "input_tokens": prompt_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 0,
    }
    cost, missing, _unknown = usage_cost_usd(usage, row)
    assert missing == ()
    assert cost is not None
    expected = Decimal(str(input_rate)) * Decimal(prompt_tokens) / Decimal("1000000")
    assert cost == expected


def test_finalize_commit_est_cost_non_null_sonnet_and_haiku() -> None:
    for model_id in ("anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-5-5"):
        row = resolve_pinned_rate(model_id)
        assert row is not None
        usage = {
            "input_tokens": 1000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 200,
        }
        cost, missing, _unknown = usage_cost_usd(usage, row)
        assert missing == ()
        assert cost is not None
        assert cost > Decimal("0")


def test_credit_gate_admits_metered_sonnet_and_haiku(tmp_path: Path) -> None:
    from anthropic_credit.ledger_init import init_ledger
    from anthropic_credit.test_config import _write_minimal_config

    ledger = tmp_path / "ledger.sqlite"
    cfg_path = _write_minimal_config(
        tmp_path,
        ledger_path=str(ledger),
        metered_models=[
            "anthropic/claude-sonnet-5",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-haiku-5-5",
        ],
    )
    cfg = load_credit_config(cfg_path)
    init_ledger(cfg)
    policy = CreditPolicy(cfg)
    body = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": "hi"}],
    }
    for model in ("anthropic/claude-sonnet-5-5", "anthropic/claude-haiku-5-5"):
        res = policy.admit(f"req-{model}", model, body, token_count=500, now=_T0)
        assert isinstance(res, Admit), getattr(res, "detail", res)
        assert res.reservation_usd is not None
