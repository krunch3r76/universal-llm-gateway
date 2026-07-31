"""Alias identity tests for collision-renamed dispatch ops."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store.dispatch_ops import _OPS, execute_op


@pytest.mark.offline
def test_impact_graph_reach_alias_identity() -> None:
    assert "impact" in _OPS
    assert "graph_reach" in _OPS
    assert _OPS["impact"] is _OPS["graph_reach"]


@pytest.mark.offline
def test_analyze_impact_claim_alignment_alias_identity() -> None:
    assert "analyze_impact" in _OPS
    assert "claim_alignment" in _OPS
    assert _OPS["analyze_impact"] is _OPS["claim_alignment"]


@pytest.mark.offline
def test_all_op_specs_resolve_to_callable() -> None:
    for key in sorted(_OPS):
        handler = _OPS[key]
        assert callable(handler), key


@pytest.mark.offline
def test_impact_and_graph_reach_execute_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_impact(*, entity_id: str, depth: int) -> dict[str, object]:
        return {"entity_id": entity_id, "depth": depth, "items": []}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_edges.impact_analysis",
        _fake_impact,
    )
    args = {"entity_id": "decision:missing-test-entity"}
    old_result = execute_op("impact", args)
    new_result = execute_op("graph_reach", args)
    assert old_result == new_result


@pytest.mark.offline
def test_analyze_impact_and_claim_alignment_execute_identically(
    monkeypatch: pytest.MonkeyPatch,
    migrated_conn: sqlite3.Connection,
) -> None:
    from cortex_store.models import ImpactAnalysisRequest

    entity_id = "decision:align-test"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="decision")

    class _FakeData:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            return {"entity_id": entity_id, "alignment_score": 0.5}

    def _fake_semantic(req: ImpactAnalysisRequest) -> _FakeData:
        return _FakeData()

    class _Ctx:
        def __enter__(self) -> sqlite3.Connection:
            return migrated_conn

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions.analyze_impact_semantic",
        _fake_semantic,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions.cortex_conn",
        _Ctx,
    )
    args = {"entity_id": entity_id, "claim": "test claim", "confidence": "believed"}
    old_result = execute_op("analyze_impact", args)
    new_result = execute_op("claim_alignment", args)
    assert old_result == new_result
