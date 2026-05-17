"""Unit tests for selection.py strategies (7 working + set_aggregation raise)."""

from __future__ import annotations

import pytest

from ..errors import SelectionError
from ..selection import STRATEGIES, select


def _fixture_items() -> list[dict]:
    return [
        {"id": 1, "claim": "oldest", "observed_at": "2020-01-01", "confidence": "suspected", "confidence_score": 0.3, "derivation_type": "inference", "entity_id": "e:1"},
        {"id": 2, "claim": "mid", "observed_at": "2024-01-01", "confidence": "believed", "confidence_score": 0.6, "derivation_type": "user_statement", "entity_id": "e:2"},
        {"id": 3, "claim": "newest high", "observed_at": "2025-06-01", "confidence": "confirmed", "confidence_score": 0.9, "derivation_type": "direct_observation", "entity_id": "e:1"},
        {"id": 4, "claim": "recent low", "observed_at": "2025-05-01", "confidence": "hypothesized", "confidence_score": 0.2, "derivation_type": "inference", "entity_id": "e:3"},
    ]


def test_strategies_constant():
    assert "all" in STRATEGIES
    assert "set_aggregation" in STRATEGIES
    assert len(STRATEGIES) == 8


def test_all_strategy():
    items = _fixture_items()
    out = select(items, "all")
    assert len(out) == 4
    assert out[0]["_selection"]["mode"] == "all"


def test_newest_n_by_observed_at():
    items = _fixture_items()
    out = select(items, "newest_n_by_observed_at", n=2)
    assert [o["id"] for o in out] == [3, 4]
    assert "newest_n:2" in out[0]["_selection"]["mode"]


def test_highest_confidence_n():
    items = _fixture_items()
    out = select(items, "highest_confidence_n", n=2)
    # highest score first: 3 (0.9), 2 (0.6)
    assert [o["id"] for o in out] == [3, 2]


def test_predicate_filter():
    items = _fixture_items()
    # our impl searches claim (as recovered prior)
    out = select(items, "predicate_filter", predicate="newest")
    assert len(out) == 1
    assert out[0]["id"] == 3


def test_derivation_filter():
    items = _fixture_items()
    out = select(items, "derivation_filter", allowed=["inference"])
    assert [o["id"] for o in out] == [1, 4]


def test_temporal_window():
    items = _fixture_items()
    out = select(items, "temporal_window", since="2024-01-01", until="2026-01-01")
    assert [o["id"] for o in out] == [2, 3, 4]


def test_composite_chain():
    items = _fixture_items()
    chain = [
        ("newest_n_by_observed_at", {"n": 3}),
        ("derivation_filter", {"allowed": ["inference"]}),
    ]
    out = select(items, "composite", chain=chain)
    # after newest 3,4,2 then filter inference -> only 4 (from the 3)
    assert [o["id"] for o in out] == [4]
    assert "composite:2" in out[0]["_selection"]["mode"]


def test_set_aggregation_raises_phase_1_0b():
    items = _fixture_items()
    with pytest.raises(SelectionError) as exc:
        select(items, "set_aggregation", set_entity_id="set:foo")
    assert "Phase 1.0b" in str(exc.value)


def test_unknown_strategy_raises():
    with pytest.raises(SelectionError):
        select([], "no_such")
