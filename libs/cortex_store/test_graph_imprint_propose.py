"""Route contract, zero-write import graph, and propose integration tests."""

from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

_MODULE = Path(__file__).resolve().parent / "routes" / "graph_imprint.py"
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


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('todo:ship', 'todo', 'Ship')"
    )
    conn.commit()


def test_route_import_graph_has_no_write_impls() -> None:
    source = _MODULE.read_text(encoding="utf-8")
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


def test_propose_response_contract(cortex_client: TestClient, migrated_conn) -> None:
    _seed(migrated_conn)
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:new", "@type": "todo", "name": "New item"}],
    }
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "normalized_patch",
        "op_plan",
        "rejects",
        "candidates",
        "proposal_id",
        "context",
    }
    assert body["context"] == "cortex.life/v1"
    assert body["op_plan"]
    assert body["rejects"] == []
    assert body["proposal_id"]


def test_propose_rejects_never_partial_plan(cortex_client: TestClient) -> None:
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:x", "delegate": "work"}],
    }
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
    body = resp.json()
    assert body["rejects"]
    assert body["op_plan"] == []
    assert body["proposal_id"] is None
    assert any(r["code"] == "refused_op" for r in body["rejects"])


def test_propose_zero_db_mutations(cortex_client: TestClient, migrated_conn) -> None:
    _seed(migrated_conn)
    before_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    before_rels = migrated_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    before_assertions = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]

    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "todo:brand-new", "@type": "todo", "name": "Would write"},
            {"@id": "todo:ship", "noted": "belief"},
        ],
    }
    resp = cortex_client.post("/graph/imprint/propose", json={"patch": patch})
    assert resp.status_code == 200

    after_entities = migrated_conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    after_rels = migrated_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    after_assertions = migrated_conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0]
    assert after_entities == before_entities
    assert after_rels == before_rels
    assert after_assertions == before_assertions


def test_events_module_registers_factories() -> None:
    mod = importlib.import_module("cortex_store.events_imprint")
    assert hasattr(mod, "graph_imprint_received")
    assert hasattr(mod, "graph_imprint_proposed")
    assert hasattr(mod, "graph_imprint_rejected")
    assert hasattr(mod, "graph_imprint_commit_received")
    assert hasattr(mod, "graph_imprint_committed")
    assert hasattr(mod, "graph_imprint_commit_rejected")
