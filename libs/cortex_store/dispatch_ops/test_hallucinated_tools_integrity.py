"""Hallucination-map and retired-alias dispatch behavior."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _OPS, execute_op
from cortex_store.dispatch_ops.workflow_hints import _CORTEX_HALLUCINATED_TOOLS


@pytest.mark.offline
def test_hallucinated_tools_map_integrity() -> None:
    op_keys = set(_OPS)
    for hallucinated, canonical in _CORTEX_HALLUCINATED_TOOLS.items():
        assert canonical in op_keys, (
            f"{hallucinated!r} → {canonical!r}: canonical not in _OPS"
        )
        assert hallucinated not in op_keys, (
            f"{hallucinated!r} must not remain a registered op"
        )


@pytest.mark.offline
def test_retired_graph_reach_unknown_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _must_not_run(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("impact handler must not run for retired graph_reach")

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_edges._op_impact",
        _must_not_run,
    )
    result = execute_op("graph_reach", {"entity_id": "decision:test"})
    assert "error" in result
    assert "Unknown cortex tool" in result["error"]
    assert result.get("hint") == "Did you mean 'impact'?"


@pytest.mark.offline
def test_retired_claim_alignment_unknown_with_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _must_not_run(**_kwargs: object) -> dict[str, object]:
        raise AssertionError(
            "analyze_impact handler must not run for retired claim_alignment"
        )

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions._op_analyze_impact",
        _must_not_run,
    )
    result = execute_op(
        "claim_alignment",
        {"entity_id": "decision:test", "claim": "x"},
    )
    assert "error" in result
    assert "Unknown cortex tool" in result["error"]
    assert result.get("hint") == "Did you mean 'analyze_impact'?"
