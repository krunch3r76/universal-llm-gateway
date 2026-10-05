"""S6 batch 8: dispatch vs typed parity for entity_retype and endeavor_repair_t1."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store import entity_rekey_core as rekey_core
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._stamped_route_resolution_testkit import (
    assert_pre_batch8_route_resolution_unchanged,
    resolve_endpoint_name,
)
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, json_encode, query
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.endeavor_birth import events as endeavor_events
from cortex_store.endeavor_birth.repair import _T1_HOST
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})


def _without_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


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
    return TestClient(create_app(db_path=str(db_path)), raise_server_exceptions=False)


def _events_snapshot(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for signal, payload in events:
        p = dict(payload)
        p.pop("timestamp", None)
        out.append((signal, p))
    return out


def _capture_record(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    bucket: list[tuple[str, dict[str, Any]]],
) -> None:
    def _capture(signal: str, **payload: Any) -> None:
        bucket.append((signal, payload))

    monkeypatch.setattr(module, "record", _capture)


def _entity_inventory(conn: sqlite3.Connection, *entity_ids: str) -> dict[str, Any]:
    ids = [eid for eid in entity_ids if eid]
    entities_out: list[dict[str, Any]] = []
    for eid in ids:
        for row in query(conn, "SELECT * FROM entities WHERE id = ?", (eid,)):
            item = dict(decode_row(row, ENTITY_JSON_FIELDS))
            item.pop("created_at", None)
            item.pop("updated_at", None)
            entities_out.append(item)
    entities_out.sort(key=lambda r: str(r.get("id")))
    aliases = query(
        conn,
        "SELECT entity_id, entity_type, alias FROM entity_aliases "
        "WHERE entity_id IN ({})".format(",".join("?" * len(ids))),
        tuple(ids),
    )
    assertions = query(
        conn,
        "SELECT entity_id, claim, confidence, derivation_type FROM assertions "
        "WHERE entity_id IN ({})".format(",".join("?" * len(ids))),
        tuple(ids),
    )
    rels = query(
        conn,
        "SELECT type, from_entity, to_entity, role, active FROM relationships "
        "WHERE from_entity IN ({0}) OR to_entity IN ({0})".format(
            ",".join("?" * len(ids))
        ),
        tuple(ids + ids),
    )
    session_edges = query(
        conn,
        "SELECT agent, from_node, to_node, edge_type FROM session_edges "
        "WHERE from_node IN ({0}) OR to_node IN ({0})".format(
            ",".join("?" * len(ids))
        ),
        tuple(ids + ids),
    )
    return {
        "entities": sorted([dict(r) for r in entities_out], key=lambda x: x.get("id")),
        "entity_aliases": sorted([dict(r) for r in aliases], key=lambda x: (x["entity_id"], x["alias"])),
        "assertions": sorted([dict(r) for r in assertions], key=lambda x: x["entity_id"]),
        "relationships": sorted(
            [dict(r) for r in rels],
            key=lambda x: (x["from_entity"], x["to_entity"], x["type"]),
        ),
        "session_edges": sorted(
            [dict(r) for r in session_edges],
            key=lambda x: (x["from_node"], x["to_node"]),
        ),
    }


def _seed_retype_success(conn: sqlite3.Connection, old_id: str = "agent_skill:batch8-parity") -> str:
    insert_entity(conn, entity_id=old_id, entity_type="agent_skill", name=old_id)
    claim = f"claim for {old_id}"
    from cortex_store.claim_hash import compute_claim_hash

    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash) VALUES (?, ?, 'believed', 'ev', 'inference', ?)",
        (old_id, claim, compute_claim_hash(old_id, claim)),
    )
    conn.commit()
    return old_id


def _normalize_retype_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_envelope(body)
    body.pop("assertion_ids_reindexed", None)  # ordinal list — compare table readback
    return body


def _error_code(body: dict[str, Any]) -> Any:
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("error", err)
    return err


def _t1_attributes(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT attributes FROM entities WHERE id = ?", (_T1_HOST,)
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def _seed_t1_pre_repair(conn: sqlite3.Connection) -> None:
    insert_entity(
        conn,
        entity_id=_T1_HOST,
        entity_type="opportunity",
        name=_T1_HOST,
        attributes=json.dumps({"bus_thread": "5129", "mode": "endeavor"}),
    )
    conn.commit()


@pytest.mark.offline
def test_entity_retype_success_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id = "agent_skill:batch8-parity"

    bind_db = tmp_path / "dispatch_retype.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_retype_success(cortex_db.cortex_conn(), old_id)
    dispatch_events: list = []
    _capture_record(monkeypatch, shared_mod, dispatch_events)
    before = _entity_inventory(cortex_db.cortex_conn(), old_id)
    dispatch_raw = execute_op(
        "entity_retype", {"entity_id": old_id, "new_type": "rule"}
    )
    assert "error" not in dispatch_raw, dispatch_raw
    new_id = str(dispatch_raw["new_id"])
    after = _entity_inventory(cortex_db.cortex_conn(), old_id, new_id)
    assert before != after
    dispatch_body = _normalize_retype_body(dispatch_raw)
    dispatch_snap = _entity_inventory(cortex_db.cortex_conn(), old_id, new_id)
    dispatch_ev = _events_snapshot(dispatch_events)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_retype"
    )
    _seed_retype_success(cortex_db.cortex_conn(), old_id)
    typed_events: list = []
    _capture_record(monkeypatch, shared_mod, typed_events)
    before_t = _entity_inventory(cortex_db.cortex_conn(), old_id, new_id)
    resp = typed_client.post(f"/entities/{old_id}/retype", json={"new_type": "rule"})
    assert resp.status_code == 200, resp.text
    new_id_t = str(resp.json()["new_id"])
    after_t = _entity_inventory(cortex_db.cortex_conn(), old_id, new_id_t)
    assert before_t != after_t
    typed_body = _normalize_retype_body(resp.json())
    assert typed_body == dispatch_body
    typed_snap = _entity_inventory(cortex_db.cortex_conn(), old_id, new_id_t)
    assert typed_snap == dispatch_snap
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_entity_retype_matter_blocked_without_force(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "finance:batch8-block"
    bind_db = tmp_path / "dispatch_block.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    insert_entity(
        cortex_db.cortex_conn(),
        entity_id=entity_id,
        entity_type="finance",
        name=entity_id,
    )
    cortex_db.cortex_conn().commit()
    dispatch_raw = execute_op(
        "entity_retype", {"entity_id": entity_id, "new_type": "work"}
    )
    assert _error_code(dispatch_raw) == "matter_genus_retype_blocked"
    dispatch_snap = _entity_inventory(cortex_db.cortex_conn(), entity_id)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_block"
    )
    insert_entity(
        cortex_db.cortex_conn(),
        entity_id=entity_id,
        entity_type="finance",
        name=entity_id,
    )
    cortex_db.cortex_conn().commit()
    resp = typed_client.post(
        f"/entities/{entity_id}/retype", json={"new_type": "work"}
    )
    assert resp.status_code == 200
    assert _error_code(resp.json()) == "matter_genus_retype_blocked"
    assert _entity_inventory(cortex_db.cortex_conn(), entity_id) == dispatch_snap


@pytest.mark.offline
def test_entity_retype_force_and_missing_entity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "finance:batch8-force"
    bind_db = tmp_path / "dispatch_force.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    insert_entity(
        cortex_db.cortex_conn(),
        entity_id=entity_id,
        entity_type="finance",
        name=entity_id,
    )
    cortex_db.cortex_conn().commit()
    dispatch_ok = execute_op(
        "entity_retype",
        {"entity_id": entity_id, "new_type": "work", "force": True},
    )
    assert dispatch_ok.get("new_id") == "work:batch8-force"

    missing = execute_op("entity_retype", {"entity_id": "missing:batch8", "new_type": "rule"})
    assert missing.get("status_code") == 404

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_force"
    )
    insert_entity(
        cortex_db.cortex_conn(),
        entity_id=entity_id,
        entity_type="finance",
        name=entity_id,
    )
    cortex_db.cortex_conn().commit()
    resp = typed_client.post(
        f"/entities/{entity_id}/retype",
        json={"new_type": "work", "force": True},
    )
    assert resp.status_code == 200
    assert resp.json().get("new_id") == "work:batch8-force"

    resp_missing = typed_client.post(
        "/entities/missing:batch8/retype",
        json={"new_type": "rule"},
    )
    assert resp_missing.status_code == 200
    assert resp_missing.json().get("status_code") == 404


@pytest.mark.offline
def test_entity_retype_failure_parity_after_first_rewrite(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "agent_skill:batch8-fail"
    original = rekey_core.rewrite_simple_column

    def fail_after_first(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        old_id: str,
        new_id: str,
    ) -> None:
        original(conn, table, column, old_id, new_id)
        raise sqlite3.OperationalError("injected after first rewrite")

    monkeypatch.setattr(rekey_core, "rewrite_simple_column", fail_after_first)

    bind_db = tmp_path / "dispatch_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_retype_success(cortex_db.cortex_conn(), entity_id)
    with pytest.raises(sqlite3.OperationalError, match="injected after first rewrite"):
        execute_op("entity_retype", {"entity_id": entity_id, "new_type": "rule"})
    dispatch_snap = _entity_inventory(cortex_db.cortex_conn(), entity_id)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_fail"
    )
    _seed_retype_success(cortex_db.cortex_conn(), entity_id)
    resp = typed_client.post(f"/entities/{entity_id}/retype", json={"new_type": "rule"})
    assert resp.status_code == 500
    typed_snap = _entity_inventory(cortex_db.cortex_conn(), entity_id)
    assert typed_snap == dispatch_snap


@pytest.mark.offline
def test_endeavor_repair_t1_first_and_second_run_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_db = tmp_path / "dispatch_repair.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    before = _t1_attributes(cortex_db.cortex_conn())
    dispatch_events: list = []
    _capture_record(monkeypatch, endeavor_events, dispatch_events)
    first_d = execute_op("endeavor_repair_t1", {})
    assert first_d.get("applied") is True
    after_first = _t1_attributes(cortex_db.cortex_conn())
    assert before != after_first
    second_d = execute_op("endeavor_repair_t1", {})
    assert second_d.get("applied") is False
    dispatch_ev = _events_snapshot(dispatch_events)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_repair"
    )
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    before_t = _t1_attributes(cortex_db.cortex_conn())
    typed_events: list = []
    _capture_record(monkeypatch, endeavor_events, typed_events)
    resp1 = typed_client.post("/endeavors/repair-t1", json={})
    assert resp1.status_code == 200
    assert resp1.json().get("applied") is True
    after_t = _t1_attributes(cortex_db.cortex_conn())
    assert before_t != after_t
    resp2 = typed_client.post("/endeavors/repair-t1", json={})
    assert resp2.json().get("applied") is False
    assert _normalize_retype_body(resp1.json()) == _normalize_retype_body(first_d)
    assert _normalize_retype_body(resp2.json()) == _normalize_retype_body(second_d)
    assert _t1_attributes(cortex_db.cortex_conn()) == after_first
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_batch8_router_resolution_no_shadow_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_pre_batch8_route_resolution_unchanged(app)
    assert (
        resolve_endpoint_name(app, "POST", "/entities/decision:probe/retype")
        == "entity_retype_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/repair-t1")
        == "endeavor_repair_t1_route"
    )
