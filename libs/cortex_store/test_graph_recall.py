"""Route contract, zero-write import graph, and hand-composed primitive diff."""

from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex_store.activation import spreading_activation
from cortex_store.claim_hash import compute_claim_hash
from cortex_store.scope_radiation import radiate_scope
from cortex_store.terminal_facts import resolve_terminal_facts

_ROUTE_MODULE = Path(__file__).resolve().parent / "routes" / "graph_recall.py"
_CARD_MODULE = Path(__file__).resolve().parent / "recall_card.py"
_WRITE_IMPORT_MARKERS = (
    "create_entity_impl",
    "update_entity_impl",
    "entity_crud",
    "ops_entities",
    "ops_relationships",
    "ops_assertions",
    "dispatch_ops",
    "_create_relationship_impl",
    "execute_op",
)

_HUB = "case:test-escrow-hub"
_DENIAL_CLAIM = (
    "Chase escrow shortage spread extension request was DENIED on 2026-04-29. "
    "Nell stated we are unable to spread the escrow shortage over 12 months."
)
_ASSOC_CLAIM = "Escrow analysis notes for related fixture entity on the hub scope."


def _assert_no_write_imports(module_path: Path) -> None:
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for marker in _WRITE_IMPORT_MARKERS:
                assert marker not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                for marker in _WRITE_IMPORT_MARKERS:
                    assert marker not in alias.name
    for marker in _WRITE_IMPORT_MARKERS:
        assert marker not in source


def _seed_entity(conn: sqlite3.Connection, entity_id: str, *, entity_type: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, entity_type, entity_id),
    )
    conn.commit()


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    assertion_id: int,
    claim: str,
    entrenchment: float = 0.8,
) -> None:
    claim_hash = compute_claim_hash(entity_id, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "review_status, entrenchment_score) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, '2026-04-29T00:00:00Z', "
        "'committed', ?)",
        (assertion_id, entity_id, claim, claim_hash, entrenchment),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()


def _insert_relationship(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    target_id: str,
    rel_type: str = "relates_to",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        (rel_type, "fixture"),
    )
    conn.execute(
        "INSERT INTO relationships "
        "(from_entity, to_entity, type, active, strength, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, 1.0, '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z')",
        (source_id, target_id, rel_type),
    )
    conn.commit()


@pytest.fixture()
def escrow_hub_fixture(migrated_conn: sqlite3.Connection) -> None:
    neighbor = "finance:test-escrow-neighbor"
    _seed_entity(migrated_conn, _HUB, entity_type="case")
    _seed_entity(migrated_conn, neighbor, entity_type="finance")
    _insert_relationship(migrated_conn, source_id=_HUB, target_id=neighbor)
    _insert_assertion(
        migrated_conn,
        entity_id=_HUB,
        assertion_id=99001,
        claim=_DENIAL_CLAIM,
    )
    _insert_assertion(
        migrated_conn,
        entity_id=neighbor,
        assertion_id=99002,
        claim=_ASSOC_CLAIM,
        entrenchment=0.9,
    )


def test_route_import_graph_has_no_write_impls() -> None:
    _assert_no_write_imports(_ROUTE_MODULE)
    _assert_no_write_imports(_CARD_MODULE)


def test_recall_requires_q_or_seeds(cortex_client: TestClient) -> None:
    resp = cortex_client.post("/graph/recall/matter", json={})
    assert resp.status_code == 422


def test_hand_composed_primitive_diff_matches_route(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
    escrow_hub_fixture,
) -> None:
    scope = radiate_scope(migrated_conn, _HUB)
    assert _HUB in scope.hop_distances

    terminal_block, _omitted = resolve_terminal_facts(migrated_conn, _HUB)
    assert terminal_block is not None
    primitive_disposition_ids = sorted(f.assertion_id for f in terminal_block.facts)

    activation = spreading_activation(migrated_conn, [_HUB], depth=1, max_results=20)
    primitive_assoc_ids = sorted(a.assertion_id for a in activation.activated)

    resp = cortex_client.post(
        "/graph/recall/matter",
        json={"seeds": [_HUB]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [r["entity_id"] for r in body["resolved"]] == [_HUB]
    route_disposition_ids = sorted(row["assertion_id"] for row in body["dispositions"])
    route_assoc_ids = sorted(row["assertion_id"] for row in body["associations"])
    assert route_disposition_ids == primitive_disposition_ids
    assert route_assoc_ids == primitive_assoc_ids


def test_continuity_route_empty_dispositions_no_vocab_null(
    cortex_client: TestClient,
    escrow_hub_fixture,
) -> None:
    resp = cortex_client.post(
        "/graph/recall/continuity",
        json={"seeds": [_HUB]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispositions"] == []
    assert "vocab_not_covered" not in body["nulls"]


def test_matter_zero_db_mutations(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
    escrow_hub_fixture,
) -> None:
    before_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    before_assertions = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    resp = cortex_client.post("/graph/recall/matter", json={"seeds": [_HUB]})
    assert resp.status_code == 200
    after_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    after_assertions = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert after_entities == before_entities
    assert after_assertions == before_assertions


def test_events_module_registers_factories() -> None:
    mod = importlib.import_module("cortex_store.events_recall")
    assert hasattr(mod, "graph_recall_card_served")
    assert hasattr(mod, "graph_recall_resolver_miss")
    assert hasattr(mod, "graph_recall_burst_not_covered")
    assert hasattr(mod, "graph_recall_escalated_to_delegate")
