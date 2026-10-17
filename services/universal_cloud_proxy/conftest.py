"""Shared pytest fixtures for universal_cloud_proxy."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest
from anthropic_credit.config import load_credit_config
from anthropic_credit.policy import CreditPolicy, set_policy


@pytest.fixture(autouse=True)
def _anthropic_credit_test_policy(tmp_path: Path) -> None:
    """Give adapter tests a generous in-memory ledger so the gate never blocks."""
    base = load_credit_config()
    ledger_path = tmp_path / "anthropic_credit.sqlite"
    metered = list(
        dict.fromkeys(
            [
                *base.metered_models,
                "anthropic/claude-sonnet-4-6",
            ]
        )
    )
    cfg = dataclasses.replace(
        base,
        ledger_path=ledger_path,
        hard_stop_usd=Decimal("999999"),
        mcp_connector_stop_usd=Decimal("999999"),
        metered_models=metered,
        available_balance=dataclasses.replace(
            base.available_balance, usd=Decimal("999999")
        ),
    )
    rates_path = tmp_path / "test_model_rates.yaml"
    rates_path.write_text(
        """
models:
  - model_id: anthropic/claude-sonnet-5
    input_rate_per_m: 2.0
    cache_write_rate_per_m: 2.5
    cache_read_rate_per_m: 0.2
    output_rate_per_m: 10.0
    source: test
    pinned: true
  - model_id: anthropic/claude-sonnet-4-6
    input_rate_per_m: 3.0
    output_rate_per_m: 15.0
    cache_write_rate_per_m: 3.75
    cache_read_rate_per_m: 0.30
    source: test
    pinned: true
""",
        encoding="utf-8",
    )
    set_policy(CreditPolicy(cfg, rates_path=rates_path))
    yield
    set_policy(None)
