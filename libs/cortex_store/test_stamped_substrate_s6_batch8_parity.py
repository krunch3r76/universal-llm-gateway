"""S6 batch 8: dispatch vs typed parity for entity_retype and endeavor_repair_t1."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store import entity_rekey_core as rekey_core
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._stamped_route_resolution_testkit import (
    assert_every_route_forward_matches_self,
    resolve_endpoint_name,
)
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.claim_hash import compute_claim_hash
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, json_encode, query
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.endeavor_birth import events as endeavor_events
from cortex_store.endeavor_birth.repair import _T1_HOST
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
_LIVE_CORTEX_DB = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)
# Volatile: wall-clock and post-commit salience recompute fields (AC2 comment).
_VOLATILE_ROW_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "observed_at",
        "accessed_at",
        "computed_at",
        "salience_score",
        "contextual_score",
        "boot_section_cache",
    }
)


def _assert_isolated_db(db_path: Path) -> None:
    assert db_path.resolve() != _LIVE_CORTEX_DB.resolve()


def _bind_isolated_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    _assert_isolated_db(db_path)
    bind_cortex_db(monkeypatch, db_path)


def _without_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _scrub_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {k: v for k, v in row.items() if k not in _VOLATILE_ROW_KEYS}
        out.append(item)
    return sorted(out, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _query_table(
    conn: sqlite3.Connection,
    table: str,
    where_sql: str,
    params: tuple[Any, ...],
) -> list[dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    rows = query(conn, f"SELECT * FROM {table} WHERE {where_sql}", params)
    return _scrub_rows([dict(r) for r in rows])


def _entity_ids_filter(*entity_ids: str) -> tuple[str, tuple[str, ...]]:
    ids = [eid for eid in entity_ids if eid]
    if not ids:
        return "0", ()
    placeholders = ",".join("?" * len(ids))
    return f"id IN ({placeholders})", tuple(ids)


def _full_retype_inventory(conn: sqlite3.Connection, *entity_ids: str) -> dict[str, Any]:
    ids = [eid for eid in entity_ids if eid]
    id_ph = ",".join("?" * len(ids)) if ids else "''"
    id_params: tuple[Any, ...] = tuple(ids)
    inv: dict[str, Any] = {}
    inv["entities"] = _query_table(conn, "entities", f"id IN ({id_ph})", id_params)
    inv["entity_aliases"] = _query_table(
        conn, "entity_aliases", f"entity_id IN ({id_ph}) OR alias IN ({id_ph})", id_params + id_params
    )
    inv["assertions"] = _query_table(
        conn, "assertions", f"entity_id IN ({id_ph})", id_params
    )
    inv["relationships"] = _query_table(
        conn,
        "relationships",
        f"from_entity IN ({id_ph}) OR to_entity IN ({id_ph})",
        id_params + id_params,
    )
    inv["surface_forms"] = _query_table(
        conn, "surface_forms", f"entity_id IN ({id_ph})", id_params
    )
    inv["tag_assignments"] = _query_table(
        conn, "tag_assignments", f"entity_id IN ({id_ph})", id_params
    )
    inv["entity_access_log"] = _query_table(
        conn, "entity_access_log", f"entity_id IN ({id_ph})", id_params
    )
    inv["entity_access_summary"] = _query_table(
        conn, "entity_access_summary", f"entity_id IN ({id_ph})", id_params
    )
    inv["assertions_fts"] = _query_table(
        conn, "assertions_fts", f"entity_id IN ({id_ph})", id_params
    )
    inv["entity_salience_cache"] = _query_table(
        conn, "entity_salience_cache", f"entity_id IN ({id_ph})", id_params
    )
    inv["session_edges"] = _query_table(
        conn,
        "session_edges",
        f"from_node IN ({id_ph}) OR to_node IN ({id_ph})",
        id_params + id_params,
    )
    inv["session_journals"] = []
    if _table_exists(conn, "session_journals"):
        for row in query(conn, "SELECT id, entity_ids FROM session_journals"):
            raw = row.get("entity_ids")
            try:
                ej = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                ej = []
            if any(eid in ej for eid in ids):
                inv["session_journals"].append(
                    {"id": row["id"], "entity_ids": sorted(ej)}
                )
    inv["journal_links"] = _query_table(
        conn, "journal_links", f"to_entity IN ({id_ph})", id_params
    )
    inv["event_chain_members"] = _query_table(
        conn, "event_chain_members", f"event_id IN ({id_ph})", id_params
    )
    inv["event_chains"] = _query_table(
        conn, "event_chains", f"root_event_id IN ({id_ph})", id_params
    )
    return inv


def _seed_retype_surfaces(
    conn: sqlite3.Connection,
    old_id: str,
    *,
    peer_id: str = "project:batch8-peer",
    entity_type: str = "agent_skill",
) -> int:
    insert_entity(conn, entity_id=peer_id, entity_type="project", name=peer_id)
    insert_entity(conn, entity_id=old_id, entity_type=entity_type, name=old_id)
    claim = f"claim for {old_id}"
    claim_hash = compute_claim_hash(old_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, evidence_uris) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, ?)",
        (
            old_id,
            claim,
            claim_hash,
            json.dumps([old_id, f"cortex:{old_id}"]),
        ),
    )
    assertion_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (old_id, peer_id),
    )
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active) "
        "VALUES (?, ?, 'evidence_for', 1)",
        (peer_id, old_id),
    )
    conn.execute(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
        (old_id, entity_type, f"alias-{old_id}"),
    )
    conn.execute(
        "INSERT INTO surface_forms (mention, entity_id, context_hash) VALUES (?, ?, ?)",
        (f"mention-{old_id}", old_id, f"ctx-{old_id}"),
    )
    conn.execute(
        "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
        "VALUES ('primary', ?, ?, 'batch8-test')",
        (old_id, assertion_id),
    )
    conn.execute(
        "INSERT INTO entity_salience_cache (entity_id, salience_score) VALUES (?, 0.5)",
        (old_id,),
    )
    if _table_exists(conn, "entity_access_log"):
        conn.execute(
            "INSERT INTO entity_access_log (entity_id, agent, operation, source) "
            "VALUES (?, 'batch8', 'read', 'test')",
            (old_id,),
        )
    if _table_exists(conn, "entity_access_summary"):
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entity_access_summary)")]
            if "entity_id" in cols:
                payload: dict[str, Any] = {"entity_id": old_id}
                if "agent" in cols:
                    payload["agent"] = "batch8"
                if "access_count" in cols:
                    payload["access_count"] = 1
                if "total_accesses" in cols:
                    payload["total_accesses"] = 1
                names = ", ".join(payload.keys())
                placeholders = ", ".join("?" * len(payload))
                conn.execute(
                    f"INSERT INTO entity_access_summary ({names}) VALUES ({placeholders})",
                    tuple(payload.values()),
                )
        except sqlite3.Error:
            pass
    conn.execute(
        "INSERT OR IGNORE INTO session_edge_types (type, description, directional) "
        "VALUES ('continues', 'batch8', 1)"
    )
    conn.execute(
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type) "
        "VALUES ('batch8-sess', 'batch8', ?, 'transcript:batch8', 'continues')",
        (old_id,),
    )
    conn.execute(
        "INSERT INTO session_journals (timestamp, agent, summary, entity_ids) "
        "VALUES ('2026-01-01T00:00:00Z', 'batch8', 'seed', ?)",
        (json.dumps([old_id, peer_id]),),
    )
    if _table_exists(conn, "reflective_journal") and _table_exists(conn, "journal_links"):
        try:
            rj = conn.execute(
                "INSERT INTO reflective_journal (timestamp, agent, summary, register) "
                "VALUES ('2026-01-01T00:00:00Z', 'batch8', 'link seed', 'test')"
            )
            entry_id = int(rj.lastrowid)
            conn.execute(
                "INSERT INTO journal_links (from_entry, to_entity, link_type) "
                "VALUES (?, ?, 'references')",
                (entry_id, old_id),
            )
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return assertion_id


def _assert_no_reference_to_id(conn: sqlite3.Connection, forbidden_id: str) -> None:
    assert not query(conn, "SELECT id FROM entities WHERE id = ?", (forbidden_id,))
    for table, col in (
        ("assertions", "entity_id"),
        ("relationships", "from_entity"),
        ("relationships", "to_entity"),
        ("entity_aliases", "entity_id"),
        ("entity_aliases", "alias"),
        ("surface_forms", "entity_id"),
        ("tag_assignments", "entity_id"),
        ("entity_access_log", "entity_id"),
        ("entity_access_summary", "entity_id"),
        ("assertions_fts", "entity_id"),
        ("entity_salience_cache", "entity_id"),
        ("session_edges", "from_node"),
        ("session_edges", "to_node"),
        ("journal_links", "to_entity"),
        ("event_chain_members", "event_id"),
        ("event_chains", "root_event_id"),
    ):
        if not _table_exists(conn, table):
            continue
        hits = query(conn, f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (forbidden_id,))
        assert not hits, f"{table}.{col} still references {forbidden_id!r}"


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    return TestClient(create_app(db_path=str(db_path)), raise_server_exceptions=False)


def _events_snapshot(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for signal, payload in events:
        # S3 shadow-validate fires on typed HTTP only — not part of handler parity.
        if signal == "mcp.cortex.dispatch.shadow":
            continue
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


def _normalize_retype_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_envelope(body)
    body.pop("assertion_ids_reindexed", None)
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


def _inventory_for_parity_compare(inv: dict[str, Any]) -> dict[str, Any]:
    """Drop post-commit salience recompute bucket — scores/timestamps differ run-to-run."""
    out = dict(inv)
    out.pop("entity_salience_cache", None)
    return out


def _assert_surfaces_changed(pre: dict[str, Any], post: dict[str, Any]) -> None:
    for key in (
        "entities",
        "assertions",
        "relationships",
        "surface_forms",
        "tag_assignments",
        "session_edges",
        "entity_salience_cache",
    ):
        assert pre.get(key) != post.get(key), f"surface {key!r} unchanged after retype"


@pytest.mark.offline
def test_entity_retype_success_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_id = "agent_skill:batch8-parity"

    bind_db = tmp_path / "dispatch_retype.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _seed_retype_surfaces(cortex_db.cortex_conn(), old_id)
    dispatch_events: list = []
    _capture_record(monkeypatch, shared_mod, dispatch_events)
    pre = _full_retype_inventory(cortex_db.cortex_conn(), old_id)
    dispatch_raw = execute_op(
        "entity_retype", {"entity_id": old_id, "new_type": "rule"}
    )
    assert "error" not in dispatch_raw, dispatch_raw
    new_id = str(dispatch_raw["new_id"])
    post = _full_retype_inventory(cortex_db.cortex_conn(), old_id, new_id)
    _assert_surfaces_changed(pre, post)
    dispatch_body = _normalize_retype_body(dispatch_raw)
    dispatch_snap = _inventory_for_parity_compare(
        _full_retype_inventory(cortex_db.cortex_conn(), old_id, new_id)
    )
    dispatch_ev = _events_snapshot(dispatch_events)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_retype"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), old_id)
    typed_events: list = []
    _capture_record(monkeypatch, shared_mod, typed_events)
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), old_id)
    resp = typed_client.post(f"/entities/{old_id}/retype", json={"new_type": "rule"})
    assert resp.status_code == 200, resp.text
    new_id_t = str(resp.json()["new_id"])
    post_t = _full_retype_inventory(cortex_db.cortex_conn(), old_id, new_id_t)
    _assert_surfaces_changed(pre_t, post_t)
    typed_body = _normalize_retype_body(resp.json())
    assert typed_body == dispatch_body
    typed_snap = _inventory_for_parity_compare(
        _full_retype_inventory(cortex_db.cortex_conn(), old_id, new_id_t)
    )
    assert typed_snap == dispatch_snap
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_entity_retype_matter_blocked_without_force_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "finance:batch8-block"
    bind_db = tmp_path / "dispatch_block.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _seed_retype_surfaces(
        cortex_db.cortex_conn(),
        entity_id,
        peer_id="project:block-peer",
        entity_type="finance",
    )
    pre = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    dispatch_events: list = []
    _capture_record(monkeypatch, shared_mod, dispatch_events)
    dispatch_raw = execute_op(
        "entity_retype", {"entity_id": entity_id, "new_type": "work"}
    )
    assert _error_code(dispatch_raw) == "matter_genus_retype_blocked"
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre
    assert _events_snapshot(dispatch_events) == []

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_block"
    )
    _seed_retype_surfaces(
        cortex_db.cortex_conn(),
        entity_id,
        peer_id="project:block-peer",
        entity_type="finance",
    )
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    typed_events: list = []
    _capture_record(monkeypatch, shared_mod, typed_events)
    resp = typed_client.post(
        f"/entities/{entity_id}/retype", json={"new_type": "work"}
    )
    assert resp.status_code == 200
    assert _error_code(resp.json()) == "matter_genus_retype_blocked"
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre_t
    assert _events_snapshot(typed_events) == []


@pytest.mark.offline
def test_entity_retype_force_true_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "finance:batch8-force"
    bind_db = tmp_path / "dispatch_force.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _seed_retype_surfaces(
        cortex_db.cortex_conn(),
        entity_id,
        peer_id="project:force-peer",
        entity_type="finance",
    )
    pre = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    dispatch_events: list = []
    _capture_record(monkeypatch, shared_mod, dispatch_events)
    dispatch_raw = execute_op(
        "entity_retype",
        {"entity_id": entity_id, "new_type": "work", "force": True},
    )
    assert dispatch_raw.get("new_id") == "work:batch8-force"
    new_id = str(dispatch_raw["new_id"])
    post = _full_retype_inventory(cortex_db.cortex_conn(), entity_id, new_id)
    _assert_surfaces_changed(pre, post)
    dispatch_snap = _inventory_for_parity_compare(
        _full_retype_inventory(cortex_db.cortex_conn(), entity_id, new_id)
    )
    dispatch_ev = _events_snapshot(dispatch_events)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_force"
    )
    _seed_retype_surfaces(
        cortex_db.cortex_conn(),
        entity_id,
        peer_id="project:force-peer",
        entity_type="finance",
    )
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    typed_events: list = []
    _capture_record(monkeypatch, shared_mod, typed_events)
    resp = typed_client.post(
        f"/entities/{entity_id}/retype",
        json={"new_type": "work", "force": True},
    )
    assert resp.status_code == 200
    new_id_t = str(resp.json()["new_id"])
    post_t = _full_retype_inventory(cortex_db.cortex_conn(), entity_id, new_id_t)
    _assert_surfaces_changed(pre_t, post_t)
    assert _normalize_retype_body(resp.json()) == _normalize_retype_body(dispatch_raw)
    assert (
        _inventory_for_parity_compare(
            _full_retype_inventory(cortex_db.cortex_conn(), entity_id, new_id_t)
        )
        == dispatch_snap
    )
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_entity_retype_missing_entity_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = "missing:batch8"
    bind_db = tmp_path / "dispatch_missing.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    pre = _full_retype_inventory(cortex_db.cortex_conn(), missing)
    dispatch_events: list = []
    _capture_record(monkeypatch, shared_mod, dispatch_events)
    dispatch_raw = execute_op(
        "entity_retype", {"entity_id": missing, "new_type": "rule"}
    )
    assert dispatch_raw.get("status_code") == 404
    assert _full_retype_inventory(cortex_db.cortex_conn(), missing) == pre
    assert _events_snapshot(dispatch_events) == []

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_missing"
    )
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), missing)
    typed_events: list = []
    _capture_record(monkeypatch, shared_mod, typed_events)
    resp = typed_client.post(f"/entities/{missing}/retype", json={"new_type": "rule"})
    assert resp.status_code == 200
    assert resp.json().get("status_code") == 404
    assert _full_retype_inventory(cortex_db.cortex_conn(), missing) == pre_t
    assert _events_snapshot(typed_events) == []


@pytest.mark.offline
def test_entity_retype_s4_new_type_missing_envelope_divergence(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known S4: dispatch 200 + error string vs typed 422; stored state identical (no op)."""
    old_id = "agent_skill:batch8-s4"
    bind_db = tmp_path / "dispatch_s4.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _seed_retype_surfaces(cortex_db.cortex_conn(), old_id)
    pre = _full_retype_inventory(cortex_db.cortex_conn(), old_id)
    dispatch_raw = execute_op("entity_retype", {"entity_id": old_id})
    assert dispatch_raw.get("error") == "new_type is required"
    assert _full_retype_inventory(cortex_db.cortex_conn(), old_id) == pre

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_s4"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), old_id)
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), old_id)
    resp = typed_client.post(f"/entities/{old_id}/retype", json={})
    assert resp.status_code == 422
    assert _full_retype_inventory(cortex_db.cortex_conn(), old_id) == pre_t


@pytest.mark.offline
def test_entity_retype_failure_parity_after_first_rewrite(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "agent_skill:batch8-fail"
    new_id = "rule:batch8-fail"
    original = rekey_core.rewrite_simple_column

    def fail_after_first(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        old_id: str,
        new: str,
    ) -> None:
        original(conn, table, column, old_id, new)
        raise sqlite3.OperationalError("injected after first rewrite")

    monkeypatch.setattr(rekey_core, "rewrite_simple_column", fail_after_first)

    bind_db = tmp_path / "dispatch_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _seed_retype_surfaces(cortex_db.cortex_conn(), entity_id)
    pre = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    with pytest.raises(sqlite3.OperationalError, match="injected after first rewrite"):
        execute_op("entity_retype", {"entity_id": entity_id, "new_type": "rule"})
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre
    _assert_no_reference_to_id(cortex_db.cortex_conn(), new_id)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_fail"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), entity_id)
    pre_t = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    resp = typed_client.post(f"/entities/{entity_id}/retype", json={"new_type": "rule"})
    assert resp.status_code == 500
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre_t
    _assert_no_reference_to_id(cortex_db.cortex_conn(), new_id)


@pytest.mark.offline
def test_endeavor_repair_t1_first_and_second_run_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_db = tmp_path / "dispatch_repair.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
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
def test_batch8_router_resolution_full_walk_no_skip(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    _assert_isolated_db(migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_every_route_forward_matches_self(app, skip_endpoint_names=frozenset())
    assert (
        resolve_endpoint_name(app, "POST", "/entities/decision:probe/retype")
        == "entity_retype_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/repair-t1")
        == "endeavor_repair_t1_route"
    )
