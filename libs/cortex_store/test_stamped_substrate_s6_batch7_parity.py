"""S6 batch 7: dispatch vs typed parity for register_skill_substrate and view_render."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._stamped_route_resolution_testkit import (
    assert_baseline_route_resolution,
    assert_pre_batch7_route_resolution_unchanged,
    resolve_endpoint_name,
)
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, json_encode, query
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.dispatch_ops import ops_composites as composites_mod
from cortex_store.dispatch_ops import ops_views as views_mod
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.dispatch_ops import execute_op
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
_LIVE_FILES_ROOT = shared_mod._FILES_ROOT


def _without_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


_ARCHIVED_AT_RE = re.compile(r"^<!-- archived_at: .+? -->\n", re.MULTILINE)
_VIEW_STAMP_TIME_RE = re.compile(r'("time":\s*")[^"]+(")')
_ISO_TS_IN_BODY_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _normalize_view_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_envelope(body)
    if "body" in body and isinstance(body["body"], str):
        scrubbed = _ARCHIVED_AT_RE.sub("", body["body"])
        scrubbed = _VIEW_STAMP_TIME_RE.sub(r"\1SCRUBBED\2", scrubbed)
        scrubbed = _ISO_TS_IN_BODY_RE.sub("SCRUBBED_TS", scrubbed)
        body = dict(body)
        body["body"] = scrubbed
    body.pop("as_of_system", None)  # volatile: register vs full stamps differ per twin
    return body


def _skill_workspace(tmp_path: Path, skill_id: str) -> tuple[str, str]:
    ws_root = tmp_path / "projects"
    skill_dir = ws_root / "universal-llm-gateway" / ".cursor" / "skills" / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")
    canonical = (
        f"workspaces://universal-llm-gateway/.cursor/skills/{skill_id}/SKILL.md"
    )
    return str(ws_root), canonical


def _bind_skill_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    skill_id: str,
) -> tuple[str, str]:
    ws_root, canonical = _skill_workspace(tmp_path, skill_id)
    monkeypatch.setenv("WORKSPACES_ROOT", ws_root)
    return ws_root, canonical


def _insert_case_entity(conn: sqlite3.Connection, case_id: str = "case:batch7-parity") -> None:
    insert_entity(conn, entity_id=case_id, entity_type="case", name=case_id)
    conn.commit()


def _seed_relationship_types(conn: sqlite3.Connection) -> None:
    for rel_type in ("keystone_of", "uses_skill"):
        conn.execute(
            "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
            (rel_type, f"batch7 {rel_type}"),
        )
    conn.commit()


def _full_register_payload(skill_id: str, canonical: str) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "skill_path": canonical,
        "case_id": "case:batch7-parity",
        "description": "batch7 parity skill",
        "trigger_phrases": ["batch7", "parity", "register"],
        "skill_binding": {"skill_class": "protocol"},
        "session_id": "cursor-2026-10-05-batch7-parity",
        "agent": "cursor-sdk",
    }


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    skill_id: str | None = None,
    files_root: Path | None = None,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    if skill_id is not None:
        _bind_skill_env(monkeypatch, tmp_path / f"ws_{suffix}", skill_id)
    if files_root is not None:
        files_root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
        monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
        assert files_root.resolve() != _LIVE_FILES_ROOT.resolve()
    return TestClient(
        create_app(db_path=str(db_path)),
        raise_server_exceptions=False,
    )


def _entity_rows(conn: sqlite3.Connection, *entity_ids: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for eid in entity_ids:
        rows = query(conn, "SELECT * FROM entities WHERE id = ?", (eid,))
        for row in rows:
            item = dict(decode_row(row, ENTITY_JSON_FIELDS))
            item.pop("created_at", None)
            item.pop("updated_at", None)
            out.append(item)
    return sorted(out, key=lambda r: str(r.get("id")))


def _relationship_rows(
    conn: sqlite3.Connection,
    skill_entity: str,
    doc_entity: str,
) -> list[dict[str, Any]]:
    rows = query(
        conn,
        "SELECT type, from_entity, to_entity, role, evidence, active "
        "FROM relationships WHERE "
        "(from_entity = ? AND to_entity = ?) OR (from_entity = ? AND to_entity = ?)"
        " ORDER BY id",
        (skill_entity, doc_entity, "case:batch7-parity", skill_entity),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("id", None)
        out.append(item)
    return out


def _session_edges_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = query(
        conn,
        "SELECT agent, from_node, to_node, edge_type, strength, edge_source, context "
        "FROM session_edges ORDER BY id",
    )
    return [dict(row) for row in rows]


def _register_db_snapshot(
    conn: sqlite3.Connection,
    skill_id: str,
) -> tuple[list, list, list]:
    skill_entity = f"agent_skill:{skill_id}"
    doc_entity = f"document:skill-{skill_id}"
    return (
        _entity_rows(conn, skill_entity, doc_entity),
        _relationship_rows(conn, skill_entity, doc_entity),
        _session_edges_snapshot(conn),
    )


def _normalize_register_response(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_envelope(body)
    body.pop("validated_path", None)  # absolute path echo — workspace root differs by suffix
    return body


def _events_snapshot(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for signal, payload in events:
        p = dict(payload)
        p.pop("timestamp", None)
        out.append((signal, p))
    return out


@pytest.fixture()
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []

    def _capture(signal: str, **payload: Any) -> None:
        events.append((signal, payload))

    monkeypatch.setattr(composites_mod, "record", _capture)
    return events


@pytest.mark.offline
def test_register_skill_substrate_create_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_events: list,
) -> None:
    skill_id = "batch7-create"
    _, canonical = _skill_workspace(tmp_path / "ws_create", skill_id)
    payload = _full_register_payload(skill_id, canonical)

    bind_db = tmp_path / "cortex_dispatch_create.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws_create" / "projects"))
    conn = cortex_db.cortex_conn()
    _seed_relationship_types(conn)
    _insert_case_entity(conn)
    dispatch_events: list = []
    monkeypatch.setattr(
        composites_mod,
        "record",
        lambda s, **p: dispatch_events.append((s, p)),
    )
    dispatch_raw = execute_op("register_skill_substrate", payload)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_register_response(dispatch_raw)
    dispatch_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)
    dispatch_ev = _events_snapshot(dispatch_events)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_create",
        skill_id=skill_id,
    )
    conn_t = cortex_db.cortex_conn()
    _seed_relationship_types(conn_t)
    _insert_case_entity(conn_t)
    typed_events: list = []
    monkeypatch.setattr(
        composites_mod,
        "record",
        lambda s, **p: typed_events.append((s, p)),
    )
    resp = typed_client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_register_response(resp.json())
    assert dispatch_body == typed_body
    typed_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)
    assert typed_snap == dispatch_snap
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_register_skill_substrate_matching_backfill_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing agent_skill only — document + keystone backfilled (non-atomic by design)."""
    skill_id = "batch7-match"
    ws_dir = tmp_path / "ws_match"
    _, canonical = _skill_workspace(ws_dir, skill_id)
    payload = _full_register_payload(skill_id, canonical)
    skill_entity = f"agent_skill:{skill_id}"

    bind_db = tmp_path / "cortex_dispatch_match.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_dir / "projects"))
    conn = cortex_db.cortex_conn()
    _insert_case_entity(conn)
    conn.execute(
        "INSERT INTO entities (id, type, name, description, attributes, source_uri) "
        "VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (
            skill_entity,
            skill_id,
            payload["description"],
            json_encode(
                {
                    "trigger_phrases": payload["trigger_phrases"],
                    "skill_binding": payload["skill_binding"],
                }
            ),
            canonical,
        ),
    )
    conn.commit()

    dispatch_raw = execute_op("register_skill_substrate", payload)
    assert dispatch_raw.get("_status") == "idempotent", dispatch_raw
    dispatch_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_match",
        skill_id=skill_id,
    )
    conn2 = cortex_db.cortex_conn()
    _insert_case_entity(conn2)
    conn2.execute(
        "INSERT INTO entities (id, type, name, description, attributes, source_uri) "
        "VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (
            skill_entity,
            skill_id,
            payload["description"],
            json_encode(
                {
                    "trigger_phrases": payload["trigger_phrases"],
                    "skill_binding": payload["skill_binding"],
                }
            ),
            canonical,
        ),
    )
    conn2.commit()
    resp = typed_client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 200, resp.text
    typed_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)
    assert typed_snap == dispatch_snap


@pytest.mark.offline
def test_register_skill_substrate_conflict_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_id = "batch7-conflict"
    ws_dir = tmp_path / "ws_conflict"
    _, canonical = _skill_workspace(ws_dir, skill_id)
    payload = _full_register_payload(skill_id, canonical)
    skill_entity = f"agent_skill:{skill_id}"

    bind_db = tmp_path / "cortex_dispatch_conflict.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_dir / "projects"))
    conn = cortex_db.cortex_conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, attributes, source_uri) "
        "VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (skill_entity, "wrong-name", "other desc", None, canonical),
    )
    conn.commit()

    dispatch_raw = execute_op("register_skill_substrate", payload)
    assert dispatch_raw.get("code") == "composite_conflict"
    dispatch_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_conflict",
        skill_id=skill_id,
    )
    conn2 = cortex_db.cortex_conn()
    conn2.execute(
        "INSERT INTO entities (id, type, name, description, attributes, source_uri) "
        "VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (skill_entity, "wrong-name", "other desc", None, canonical),
    )
    conn2.commit()
    resp = typed_client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 200
    assert resp.json().get("code") == "composite_conflict"
    typed_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)
    assert typed_snap == dispatch_snap


@pytest.mark.offline
def test_register_skill_substrate_failure_parity_after_skill_insert(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.entity_crud import create_entity_impl

    skill_id = "batch7-fail"
    ws_dir = tmp_path / "ws_fail"
    _, canonical = _skill_workspace(ws_dir, skill_id)
    payload = _full_register_payload(skill_id, canonical)
    original = create_entity_impl
    state = {"skill_written": False}

    def fail_after_skill(conn: sqlite3.Connection, body: dict[str, Any], **kw: Any) -> Any:
        if body.get("type") == "agent_skill":
            state["skill_written"] = True
            return original(conn, body, **kw)
        if state["skill_written"]:
            raise sqlite3.OperationalError("injected after skill insert")
        return original(conn, body, **kw)

    monkeypatch.setattr(composites_mod, "create_entity_impl", fail_after_skill)

    bind_db = tmp_path / "cortex_dispatch_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_dir / "projects"))
    conn_f = cortex_db.cortex_conn()
    _seed_relationship_types(conn_f)
    _insert_case_entity(conn_f)
    with pytest.raises(sqlite3.OperationalError, match="injected after skill insert"):
        execute_op("register_skill_substrate", payload)
    dispatch_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_fail",
        skill_id=skill_id,
    )
    conn_ft = cortex_db.cortex_conn()
    _seed_relationship_types(conn_ft)
    _insert_case_entity(conn_ft)
    resp = typed_client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 500
    typed_snap = _register_db_snapshot(cortex_db.cortex_conn(), skill_id)
    assert typed_snap == dispatch_snap


def _seed_view_doc(
    files_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    doc_id: str = "document:batch7-view",
    case_id: str = "case:batch7-view",
) -> str:
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
    _op_entity_create(id=case_id, type="case", name="view case")
    _op_entity_create(
        id=doc_id,
        type="document",
        name="view doc",
        source_uri="cortex://notes/views/batch7-view.md",
    )
    reg = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "register",
            "root_id": case_id,
            "view_profile": "matter_charter",
        },
    )
    assert "error" not in reg, reg
    return (reg.get("stamp") or {}).get("time") or "2026-07-12T12:00:00+00:00"


def _seed_two_asof_stamps(
    files_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
    assert files_root.resolve() != _LIVE_FILES_ROOT.resolve()
    as_of_early = _seed_view_doc(files_root, monkeypatch)
    full = execute_op(
        "view_render",
        {"document_id": "document:batch7-view", "mode": "full"},
    )
    assert "error" not in full, full
    as_of_late = (full.get("stamp") or {}).get("time") or as_of_early
    return as_of_early, as_of_late


@pytest.mark.offline
def test_view_render_read_asof_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_files = tmp_path / "files_dispatch_view"
    dispatch_files.mkdir()
    bind_db = tmp_path / "cortex_dispatch_view.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    as_of_early, as_of_late = _seed_two_asof_stamps(dispatch_files, monkeypatch)

    dispatch_early = _normalize_view_body(
        execute_op(
            "view_render",
            {
                "document_id": "document:batch7-view",
                "mode": "read_asof",
                "as_of_system": as_of_early,
            },
        )
    )
    dispatch_late = _normalize_view_body(
        execute_op(
            "view_render",
            {
                "document_id": "document:batch7-view",
                "mode": "read_asof",
                "as_of_system": as_of_late,
            },
        )
    )
    dispatch_missing = _normalize_view_body(
        execute_op(
            "view_render",
            {
                "document_id": "document:missing-view",
                "mode": "read_asof",
                "as_of_system": as_of_early,
            },
        )
    )

    typed_files = tmp_path / "files_typed_view"
    typed_files.mkdir()
    typed_db = tmp_path / "cortex_typed_view.db"
    copy_template_db(migrated_db_template, typed_db)
    bind_cortex_db(monkeypatch, typed_db)
    as_of_early_t, as_of_late_t = _seed_two_asof_stamps(typed_files, monkeypatch)
    typed_client = TestClient(create_app(db_path=str(typed_db)))

    r_early = typed_client.get(
        "/views/document:batch7-view",
        params={"asof": as_of_early_t, "mode": "read_asof"},
    )
    r_late = typed_client.get(
        "/views/document:batch7-view",
        params={"asof": as_of_late_t, "mode": "read_asof"},
    )
    r_missing = typed_client.get(
        "/views/document:missing-view",
        params={"asof": as_of_early_t, "mode": "read_asof"},
    )
    assert r_early.status_code == 200
    assert r_late.status_code == 200
    assert r_missing.status_code == 200

    assert _normalize_view_body(r_early.json()) == dispatch_early
    assert _normalize_view_body(r_late.json()) == dispatch_late
    assert _normalize_view_body(r_missing.json()) == dispatch_missing


@pytest.mark.offline
def test_batch7_router_resolution_no_shadow_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_pre_batch7_route_resolution_unchanged(app)
    assert_baseline_route_resolution(app)
    assert (
        resolve_endpoint_name(app, "POST", "/skills/register-substrate")
        == "register_skill_substrate_route"
    )
    assert (
        resolve_endpoint_name(
            app,
            "GET",
            "/views/document:probe-view",
        )
        == "view_render_route"
    )
