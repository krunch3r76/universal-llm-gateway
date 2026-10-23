"""Tier0 entity cards + tier1 RRF (k=20) search fusion (S2)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.routes.assertions._search import _rrf_fuse, _tier0_strict_entity_cards


@pytest.fixture()
def migrated_db(migrated_db_path, monkeypatch: pytest.MonkeyPatch):
    bind_cortex_db(monkeypatch, migrated_db_path)
    return migrated_db_path


def test_rrf_k20_prefers_dual_list_hits() -> None:
    """RRF at k=20: items ranked on both lists beat single-list-only peers."""
    fts_order = ["assertion:10", "assertion:20", "assertion:30"]
    vector_order = ["assertion:20", "assertion:10", "assertion:40"]
    scores = _rrf_fuse([(fts_order, 1.0), (vector_order, 1.0)], k=20)
    assert scores["assertion:20"] > scores["assertion:30"]
    assert scores["assertion:20"] > scores["assertion:40"]
    assert scores["assertion:10"] > scores["assertion:30"]


def test_tier0_cap_and_bm25_threshold(
    migrated_db_path,
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    """Strict tier0 caps at limit//2 and drops weak identity BM25 (<0.5×best)."""
    for idx in range(4):
        _op_entity_create(
            id=f"document:tier0-cap-{idx}",
            type="document",
            name=f"Cap Token Alpha {idx}",
            description="tier0 cap fixture",
        )
    # Weak name match — should not pass 0.5×best threshold vs strong hits.
    _op_entity_create(
        id="document:tier0-weak",
        type="document",
        name="Unrelated Label",
        description="Cap Token Alpha mentioned only in description body",
    )

    cards = _tier0_strict_entity_cards(
        migrated_conn,
        "Cap Token Alpha",
        entity_type=None,
        limit=5,
    )
    assert len(cards) <= 5 // 2
    assert cards
    assert all(c["kind"] == "entity" for c in cards)
    assert all(c["retrieval_source"] == "entity" for c in cards)
    ids = {c["entity_id"] for c in cards}
    assert "document:tier0-weak" not in ids
    assert "assertion_count" in cards[0]


def test_search_items_expose_kind(
    cortex_client: TestClient,
    migrated_db,  # noqa: ARG001
) -> None:
    """Dispatch search returns kind=entity and kind=assertion without synthetic ids."""
    _op_entity_create(
        id="runbook:search-kind-entity",
        type="runbook",
        name="Search Kind Entity Runbook",
        description="identity strict lane fixture",
    )
    assert_result = _op_assert(
        entity_id="runbook:search-kind-entity",
        claim="search kind assertion fixture unique phrase zeta",
        confidence="confirmed",
        evidence="unit test",
        derivation_type="direct_observation",
    )
    assert "error" not in assert_result

    entity_resp = cortex_client.post(
        "/dispatch",
        json={
            "tool": "search",
            "arguments": {"query": "Search Kind Entity Runbook", "limit": 5},
        },
    )
    assert entity_resp.status_code == 200, entity_resp.text
    entity_hits = entity_resp.json().get("items") or []
    assert entity_hits
    entity_row = entity_hits[0]
    assert entity_row.get("kind") == "entity"
    assert entity_row.get("entity_id") == "runbook:search-kind-entity"
    assert "id" not in entity_row
    hints = entity_row.get("action_hints") or []
    assert any("entity_get" in str(h.get("action", "")) for h in hints)

    assert_resp = cortex_client.post(
        "/dispatch",
        json={
            "tool": "search",
            "arguments": {
                "query": "search kind assertion fixture unique phrase zeta",
                "limit": 5,
            },
        },
    )
    assert assert_resp.status_code == 200, assert_resp.text
    assertion_hits = assert_resp.json().get("items") or []
    assert any(h.get("kind") == "assertion" for h in assertion_hits)
    for row in assertion_hits:
        if row.get("kind") == "assertion":
            assert int(row["id"]) > 0
