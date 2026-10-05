"""S6 batch 4: dispatch vs typed parity for entities/relationships bulk upserts."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._stamped_route_resolution_testkit import (
    assert_baseline_route_resolution,
    resolve_endpoint_name,
)
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, query
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.dispatch_ops import execute_op
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
# Per-item ids are store-local; actions and stable fields are compared.
_VOLATILE_BULK_ITEM_KEYS = frozenset({"relationship_id"})
_VOLATILE_ENTITY_ROW_KEYS = frozenset({"created_at", "updated_at"})
_REL_ROW_COLS = (
    "type",
    "from_entity",
    "to_entity",
    "role",
    "strength",
    "evidence",
    "chunk_id",
    "valid_from",
    "valid_until",
    "source_uri",
    "session_id",
    "agent",
    "active",
)


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _normalize_bulk_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    out = copy.deepcopy(body)
    items = out.get("items")
    if isinstance(items, list):
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            row = {k: v for k, v in item.items() if k not in _VOLATILE_BULK_ITEM_KEYS}
            normalized_items.append(row)
        out["items"] = normalized_items
    return out


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    return TestClient(create_app(db_path=str(db_path)))


def _seed_relationship_type(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        ("child_of", "Model belongs to family"),
    )
    conn.commit()


def _entity_row(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    assert rows, entity_id
    data = decode_row(rows[0], ENTITY_JSON_FIELDS)
    return {
        k: v for k, v in data.items() if k not in _VOLATILE_ENTITY_ROW_KEYS
    }


def _relationship_rows(
    conn: sqlite3.Connection, *, source_id: str, target_id: str, type_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {', '.join(_REL_ROW_COLS)} FROM relationships "
        "WHERE from_entity = ? AND to_entity = ? AND type = ? ORDER BY id",
        (source_id, target_id, type_id),
    ).fetchall()
    return [dict(zip(_REL_ROW_COLS, r, strict=True)) for r in rows]


def _entities_bulk_fixture() -> dict[str, Any]:
    return {
        "if_exists": "update",
        "entities": [
            {
                "id": "model:gpt-batch4",
                "type": "model",
                "name": "GPT batch4",
                "description": "batch4 create path",
                "status": "unsubstantiated",
                "workflow_state": "active",
                "attributes": {"tier": "parity", "unsuitable_for": ["bulk-gap"]},
                "aliases": ["openai/gpt-batch4"],
                "source_uri": "file:///tmp/batch4-new.md",
                "notes": "batch4 notes new",
                "retention_policy": "permanent",
                "retention_ttl_days": 30,
            },
            {
                "id": "family:openai-batch4",
                "type": "family",
                "name": "OpenAI batch4",
                "description": "batch4 update path",
                "attributes": {"region": "us"},
                "aliases": ["openai-batch4"],
                "if_exists": "update",
            },
        ],
    }


def _relationships_bulk_fixture() -> dict[str, Any]:
    return {
        "if_exists": "update",
        "resolve_aliases": True,
        "relationships": [
            {
                "source_id": "openai/gpt-batch4",
                "target_id": "family:openai-batch4",
                "type_id": "child_of",
                "role": "initial",
                "strength": 0.8,
                "evidence": "batch4 rel evidence",
                "valid_from": "2026-01-01",
                "valid_until": "2027-01-01",
                "source_uri": "file:///tmp/batch4-rel.md",
                "session_id": "s6-batch4-parity",
                "agent": "cursor-sdk",
            },
            {
                "source_id": "model:gpt-batch4",
                "target_id": "family:openai-batch4",
                "type_id": "child_of",
                "role": "canonical",
                "strength": 1.0,
                "evidence": "batch4 rel update",
                "session_id": "s6-batch4-parity-2",
                "agent": "pytest",
                "resolve_aliases": False,
                "if_exists": "update",
            },
        ],
    }


def _seed_entities_bulk_existing(conn: sqlite3.Connection) -> None:
    insert_entity(
        conn,
        entity_id="family:openai-batch4",
        entity_type="family",
        name="OpenAI batch4 seed",
        description="seed desc",
        status="unsubstantiated",
        workflow_state="active",
        attributes=json.dumps({"region": "eu"}),
    )
    conn.commit()


@pytest.mark.offline
def test_entities_bulk_upsert_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _entities_bulk_fixture()
    entity_ids = ["model:gpt-batch4", "family:openai-batch4"]

    bind_db = tmp_path / "cortex_dispatch_ent_bulk.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_entities_bulk_existing(cortex_db.cortex_conn())
    dispatch_raw = execute_op("entities_bulk_upsert", payload)
    assert dispatch_raw.get("rolled_back") is False, dispatch_raw
    dispatch_body = _normalize_bulk_body(dispatch_raw)
    dispatch_rows = [_entity_row(cortex_db.cortex_conn(), eid) for eid in entity_ids]

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_ent_bulk"
    )
    _seed_entities_bulk_existing(cortex_db.cortex_conn())
    resp = typed_client.post("/entities/bulk", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_bulk_body(resp.json())
    assert dispatch_body == typed_body
    typed_rows = [_entity_row(cortex_db.cortex_conn(), eid) for eid in entity_ids]
    assert dispatch_rows == typed_rows


@pytest.mark.offline
def test_relationships_bulk_upsert_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ent_payload = _entities_bulk_fixture()
    rel_payload = _relationships_bulk_fixture()

    bind_db = tmp_path / "cortex_dispatch_rel_bulk.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_relationship_type(cortex_db.cortex_conn())
    _seed_entities_bulk_existing(cortex_db.cortex_conn())
    execute_op("entities_bulk_upsert", ent_payload)
    dispatch_raw = execute_op("relationships_bulk_upsert", rel_payload)
    assert dispatch_raw.get("rolled_back") is False, dispatch_raw
    dispatch_body = _normalize_bulk_body(dispatch_raw)
    dispatch_rows = _relationship_rows(
        cortex_db.cortex_conn(),
        source_id="model:gpt-batch4",
        target_id="family:openai-batch4",
        type_id="child_of",
    )

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_rel_bulk"
    )
    _seed_relationship_type(cortex_db.cortex_conn())
    _seed_entities_bulk_existing(cortex_db.cortex_conn())
    execute_op("entities_bulk_upsert", ent_payload)
    resp = typed_client.post("/relationships/bulk", json=rel_payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_bulk_body(resp.json())
    assert dispatch_body == typed_body
    typed_rows = _relationship_rows(
        cortex_db.cortex_conn(),
        source_id="model:gpt-batch4",
        target_id="family:openai-batch4",
        type_id="child_of",
    )
    assert dispatch_rows == typed_rows
    assert dispatch_rows[-1]["role"] == "canonical"


def _entities_bulk_failure_payload() -> dict[str, Any]:
    return {
        "entities": [
            {"id": "model:fail-a", "type": "model", "name": "Fail A"},
            {"id": "model:fail-b", "type": "model", "name": "Fail B"},
        ],
        "if_exists": "fail",
    }


def _seed_entities_bulk_failure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, created_at, updated_at) "
        "VALUES ('model:fail-b', 'model', 'Existing B', 't0', 't0')"
    )
    conn.commit()


def _entity_id_set(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT id FROM entities").fetchall()}


@pytest.mark.offline
def test_entities_bulk_upsert_failure_parity_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _entities_bulk_failure_payload()

    bind_db = tmp_path / "cortex_dispatch_ent_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_entities_bulk_failure(cortex_db.cortex_conn())
    dispatch_raw = execute_op("entities_bulk_upsert", payload)
    assert dispatch_raw.get("rolled_back") is True
    dispatch_body = _normalize_bulk_body(dispatch_raw)
    dispatch_ids = _entity_id_set(cortex_db.cortex_conn())

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_ent_fail"
    )
    _seed_entities_bulk_failure(cortex_db.cortex_conn())
    resp = typed_client.post("/entities/bulk", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_bulk_body(resp.json())
    assert dispatch_body == typed_body
    assert _entity_id_set(cortex_db.cortex_conn()) == dispatch_ids


@pytest.mark.offline
def test_relationships_bulk_upsert_failure_parity_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "if_exists": "fail",
        "relationships": [
            {
                "source_id": "model:rel-fail-a",
                "target_id": "family:rel-fail-b",
                "type_id": "child_of",
            },
            {
                "source_id": "model:rel-fail-a",
                "target_id": "family:rel-fail-b",
                "type_id": "child_of",
            },
        ],
    }

    def seed(conn: sqlite3.Connection) -> None:
        _seed_relationship_type(conn)
        conn.execute(
            "INSERT INTO entities (id, type, name, created_at, updated_at) VALUES "
            "('model:rel-fail-a', 'model', 'A', 't0', 't0'), "
            "('family:rel-fail-b', 'family', 'B', 't0', 't0')"
        )
        conn.execute(
            "INSERT INTO relationships (type, from_entity, to_entity, role, strength, "
            "created_at, updated_at, active) VALUES "
            "('child_of', 'model:rel-fail-a', 'family:rel-fail-b', 'seed', 1.0, 't0', 't0', 1)"
        )
        conn.commit()

    bind_db = tmp_path / "cortex_dispatch_rel_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    seed(cortex_db.cortex_conn())
    dispatch_raw = execute_op("relationships_bulk_upsert", payload)
    assert dispatch_raw.get("rolled_back") is True
    dispatch_body = _normalize_bulk_body(dispatch_raw)
    dispatch_count = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM relationships WHERE active = 1"
    ).fetchone()[0]

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_rel_fail"
    )
    seed(cortex_db.cortex_conn())
    resp = typed_client.post("/relationships/bulk", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_bulk_body(resp.json())
    assert dispatch_body == typed_body
    typed_count = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM relationships WHERE active = 1"
    ).fetchone()[0]
    assert int(dispatch_count) == int(typed_count) == 1


@pytest.mark.offline
def test_batch4_router_resolution_no_shadow_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_baseline_route_resolution(app)
    assert resolve_endpoint_name(app, "POST", "/entities/bulk") == "entities_bulk_upsert_route"
    assert (
        resolve_endpoint_name(app, "POST", "/relationships/bulk")
        == "relationships_bulk_upsert_route"
    )
