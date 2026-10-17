"""Tests for model rate table pinned lookup and long-prompt tiers."""

from __future__ import annotations

import pytest

from event_store import model_rate_table as mrt
from event_store.dispatch_economics_pricing import _rate_x_tokens
from event_store.model_rate_table import (
    ModelRateRow,
    clear_catalog_rows_for_tests,
    load_manual_rows,
    resolve_pinned_rate,
    resolve_rate,
    save_catalog_rows_to_disk,
)


@pytest.fixture(scope="module", autouse=True)
def _hermetic_catalog_path(tmp_path_factory: pytest.TempPathFactory):
    catalog = tmp_path_factory.mktemp("rates") / "model_rates_catalog.yaml"
    original = mrt._CATALOG_PATH
    mrt._CATALOG_PATH = catalog
    clear_catalog_rows_for_tests()
    yield catalog
    mrt._CATALOG_PATH = original
    clear_catalog_rows_for_tests()


@pytest.fixture(autouse=True)
def _clear_catalog_rows() -> None:
    clear_catalog_rows_for_tests()
    yield
    clear_catalog_rows_for_tests()


def test_resolve_pinned_rate_sonnet_5_seed() -> None:
    row = resolve_pinned_rate("anthropic/claude-sonnet-5")
    assert row is not None
    assert row.pinned is True
    assert row.input_rate_per_m == 2.0
    assert row.cache_write_rate_per_m == 2.5
    assert row.cache_read_rate_per_m == 0.2
    assert row.output_rate_per_m == 10.0
    assert row.source == "manual_seed"


def test_resolve_pinned_rate_does_not_follow_alias() -> None:
    assert resolve_pinned_rate("anthropic/claude-opus-5") is None
    _rows, aliases = load_manual_rows()
    assert aliases.get("anthropic/claude-opus-5") == "openrouter/anthropic/claude-opus-5"
    save_catalog_rows_to_disk(
        {
            "openrouter/anthropic/claude-opus-5": {
                "model_id": "openrouter/anthropic/claude-opus-5",
                "input_rate_per_m": 3.0,
                "output_rate_per_m": 15.0,
                "source": "catalog_refresh",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        }
    )
    aliased = resolve_rate("anthropic/claude-opus-5")
    assert aliased is not None
    assert aliased.model_id == "openrouter/anthropic/claude-opus-5"


def test_long_prompt_tier_boundary_at_100k() -> None:
    rate = ModelRateRow(
        model_id="test/long-tier",
        input_rate_per_m=1.0,
        output_rate_per_m=2.0,
        source="test",
        updated_at="2026-01-01T00:00:00+00:00",
        long_prompt_threshold_tokens=100_000,
        long_input_rate_per_m=5.0,
        long_output_rate_per_m=10.0,
    )
    at_threshold = {
        "prompt_tokens": 100_000,
        "completion_tokens": 0,
        "total_tokens_basis": "exclusive_sum",
    }
    over_threshold = {
        "prompt_tokens": 100_001,
        "completion_tokens": 0,
        "total_tokens_basis": "exclusive_sum",
    }
    base_cost = _rate_x_tokens(at_threshold, rate)
    long_cost = _rate_x_tokens(over_threshold, rate)
    assert base_cost == 0.1
    assert long_cost == pytest.approx(0.500005)
