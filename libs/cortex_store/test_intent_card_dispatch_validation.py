"""Required cases 3 + 5 — dispatch-surface validation.

Covers:
  * Case 3 — top_k=0 (and out-of-range) rejected by dispatch op
  * Case 5 — intent in {cluster, impact} returns 501-style hint at dispatch
  * Bonus — unknown intent rejected at dispatch

Split from ``test_intent_card.py`` (SLOC waiver assertion 8521 on
``spec:cortex-v2.4``) by required-case grouping.
"""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.ops_entities import _op_entity_get


def test_top_k_zero_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=0)
    assert "error" in result
    assert "top_k" in result["error"]


def test_top_k_negative_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=-3)
    assert "error" in result


def test_top_k_above_cap_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="card", top_k=51)
    assert "error" in result


@pytest.mark.parametrize("intent", ["cluster", "impact"])
def test_deferred_intents_rejected_at_dispatch(intent: str) -> None:
    result = _op_entity_get(entity_id="todo:anything", intent=intent)
    assert "error" in result
    assert "reserved" in result["error"]
    assert result.get("supported_intents") == ["full", "card"]


def test_unknown_intent_rejected_at_dispatch() -> None:
    result = _op_entity_get(entity_id="todo:anything", intent="bogus")
    assert "error" in result
    assert "Unknown intent" in result["error"]
