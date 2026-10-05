"""S6 batch 6: dispatch vs typed parity for endeavor rows and view_render."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._stamped_route_resolution_testkit import (
    assert_baseline_route_resolution,
    resolve_endpoint_name,
)
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, json_encode, query
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_entities as ops_entities_mod
from cortex_store.dispatch_ops import ops_views as ops_views_mod
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
# Nested assertion payloads: ids and timestamps differ run-to-run
_VOLATILE_ASSERTION_KEYS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "observed_at",
        "valid_from",
        "valid_until",
    }
)
# view_render: stamp time and rev bump on register/full paths
_VOLATILE_VIEW_KEYS = frozenset(
    {
        "view_rev",
        "archived_revision_uri",
        "core_hash",
        "stamp",
        "sections_repaired",
        # echo of request timestamp — each twin uses its own register stamp
        "as_of_system",
    }
)
_LIVE_FILES_ROOT = shared_mod._FILES_ROOT
# archived_at / view-stamp times differ run-to-run; core markdown structure is parity signal
_VIEW_BODY_VOLATILE_RE = re.compile(
    r"<!-- archived_at:.*?-->\n|<!-- view-stamp:.*?-->\n",
    re.DOTALL,
)


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _scrub_assertion_tree(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in _VOLATILE_ASSERTION_KEYS:
                continue
            out[k] = _scrub_assertion_tree(v)
        return out
    if isinstance(value, list):
        return [_scrub_assertion_tree(v) for v in value]
    return value


def _normalize_endeavor_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    if "assertion" in body:
        body = dict(body)
        body["assertion"] = _scrub_assertion_tree(body["assertion"])
    for key in ("assertion_id", "pin", "superseded_assertion_id", "disposing_assertion_id"):
        if key in body:
            body = dict(body)
            body.pop(key, None)
    return body


def _normalize_view_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    out = {k: v for k, v in body.items() if k not in _VOLATILE_VIEW_KEYS}
    if isinstance(out.get("body"), str):
        out = dict(out)
        out["body"] = _VIEW_BODY_VOLATILE_RE.sub("", out["body"])
    return out


def _bind_view_files(monkeypatch: pytest.MonkeyPatch, files_root: Path) -> None:
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(ops_views_mod, "_FILES_ROOT", files_root)


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    files_root: Path | None = None,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    if files_root is not None:
        _bind_view_files(monkeypatch, files_root)
    return TestClient(create_app(db_path=str(db_path)))


def _insert_endeavor_host(conn: sqlite3.Connection, host_id: str) -> None:
    insert_entity(
        conn,
        entity_id=host_id,
        entity_type="opportunity",
        name=host_id,
        attributes=json_encode(
            {
                "mode": "endeavor",
                "ring_thread": "15231",
                "endeavor_charter_uri": "cortex://notes/system/threads/batch6-charter.md",
            }
        ),
    )
    conn.commit()


def _write_row_payload(host: str) -> dict[str, Any]:
    return {
        "host": host,
        "fields": {
            "row_id": "batch6-r1",
            "material": True,
            "disposition": None,
            "reason": "batch6 parity pending pin",
            "affects": ["deliverable:batch6-d1"],
            "authority": "agent:batch6",
            "theme": "parity",
        },
    }


def _assertion_rows_for_host(conn: sqlite3.Connection, host: str) -> list[dict[str, Any]]:
    rows = query(
        conn,
        "SELECT * FROM assertions WHERE entity_id = ? ORDER BY id",
        (host,),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(decode_row(row, {"attributes", "evidence_uris"}))
        item.pop("id", None)
        item.pop("created_at", None)
        item.pop("updated_at", None)
        item.pop("observed_at", None)
        attrs = item.get("attributes")
        if isinstance(attrs, dict):
            attrs = dict(attrs)
            attrs.pop("pin", None)
            item["attributes"] = attrs
        out.append(item)
    return out


@pytest.mark.offline
def test_endeavor_write_row_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = "opportunity:batch6-write-host"
    payload = _write_row_payload(host)

    bind_db = tmp_path / "cortex_dispatch_write_row.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    dispatch_raw = execute_op("endeavor_write_row", payload)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_endeavor_body(dispatch_raw)
    dispatch_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write_row"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    resp = typed_client.post("/endeavors/strategy-rows", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_endeavor_body(resp.json())
    assert dispatch_body == typed_body
    typed_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)
    assert typed_rows == dispatch_rows


@pytest.mark.offline
def test_endeavor_dispose_row_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = "opportunity:batch6-dispose-host"
    write_payload = _write_row_payload(host)
    write_payload["fields"]["row_id"] = "batch6-dispose-r1"
    dispose_payload = {
        "host": host,
        "row_id": "batch6-dispose-r1",
        "disposition": "express",
        "reason": "batch6 dispose parity",
        "authority": "agent:batch6",
    }

    bind_db = tmp_path / "cortex_dispatch_dispose.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    execute_op("endeavor_write_row", write_payload)
    dispatch_raw = execute_op("endeavor_dispose_row", dispose_payload)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_endeavor_body(dispatch_raw)
    dispatch_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_dispose"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    typed_client.post("/endeavors/strategy-rows", json=write_payload)
    resp = typed_client.post("/endeavors/strategy-rows/dispose", json=dispose_payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_endeavor_body(resp.json())
    assert dispatch_body == typed_body
    typed_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)
    assert typed_rows == dispatch_rows


@pytest.mark.offline
def test_endeavor_write_row_failure_parity_after_assertion_insert(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial failure: assertion row exists before pin UPDATE (write_row.py:96-106)."""
    host = "opportunity:batch6-write-fail-host"
    payload = _write_row_payload(host)
    payload["fields"]["row_id"] = "batch6-fail-r1"

    import cortex_store.routes.assertions._create as create_mod

    original_create = create_mod._create_assertion_impl

    def create_then_fail(body: dict[str, Any]) -> dict[str, Any]:
        result = original_create(body)
        raise sqlite3.OperationalError("injected after assertion insert")

    monkeypatch.setattr(create_mod, "_create_assertion_impl", create_then_fail)

    bind_db = tmp_path / "cortex_dispatch_write_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    dispatch_raw = execute_op("endeavor_write_row", payload)
    assert "error" in dispatch_raw
    dispatch_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write_fail"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    resp = typed_client.post("/endeavors/strategy-rows", json=payload)
    assert resp.status_code == 200, resp.text
    typed_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)
    assert typed_rows == dispatch_rows
    assert len(dispatch_rows) >= 1


@pytest.mark.offline
def test_endeavor_dispose_row_failure_parity_supersede_surface(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-step supersede surface (write_row.py:161 _supersede_assertion_impl)."""
    host = "opportunity:batch6-dispose-fail-host"
    write_payload = _write_row_payload(host)
    write_payload["fields"]["row_id"] = "batch6-sup-fail-r1"
    dispose_payload = {
        "host": host,
        "row_id": "batch6-sup-fail-r1",
        "disposition": "express",
        "authority": "agent:batch6",
    }

    import cortex_store.routes.assertions._supersede as supersede_mod

    original_supersede = supersede_mod._supersede_assertion_impl

    def supersede_then_fail(body: dict[str, Any]) -> dict[str, Any]:
        result = original_supersede(body)
        raise RuntimeError("injected after supersede")

    monkeypatch.setattr(supersede_mod, "_supersede_assertion_impl", supersede_then_fail)

    bind_db = tmp_path / "cortex_dispatch_dispose_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    execute_op("endeavor_write_row", write_payload)
    dispatch_raw = execute_op("endeavor_dispose_row", dispose_payload)
    assert "error" in dispatch_raw
    dispatch_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_dispose_fail"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    typed_client.post("/endeavors/strategy-rows", json=write_payload)
    resp = typed_client.post("/endeavors/strategy-rows/dispose", json=dispose_payload)
    assert resp.status_code == 200, resp.text
    typed_rows = _assertion_rows_for_host(cortex_db.cortex_conn(), host)
    assert typed_rows == dispatch_rows


def _seed_view_doc(files_root: Path) -> tuple[str, str]:
    root = ops_entities_mod._op_entity_create(
        id="case:batch6-view", type="case", name="Batch6 View Case"
    )
    assert "error" not in root
    doc_id = "document:batch6-charter"
    doc = ops_entities_mod._op_entity_create(
        id=doc_id,
        type="document",
        name="Batch6 Charter",
        source_uri="cortex://notes/views/batch6-charter.md",
    )
    assert "error" not in doc
    files_root.mkdir(parents=True, exist_ok=True)
    return "case:batch6-view", doc_id


@pytest.mark.offline
def test_view_render_read_asof_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_files = tmp_path / "files_dispatch_view"
    typed_files = tmp_path / "files_typed_view"
    dispatch_files.mkdir()
    typed_files.mkdir()

    bind_db = tmp_path / "cortex_dispatch_view.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_view_files(monkeypatch, dispatch_files)
    root_id, doc_id = _seed_view_doc(dispatch_files)
    reg = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "agent": "cursor",
            "session_id": "batch6-session",
        },
    )
    assert "error" not in reg, reg
    full = execute_op("view_render", {"document_id": doc_id, "mode": "full"})
    assert "error" not in full, full
    stamp = reg.get("stamp") or {}
    as_of_before_dispatch = stamp.get("time") or "2026-07-12T12:00:00+00:00"
    as_of_after = "2099-01-01T00:00:00+00:00"

    read_before_dispatch = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "read_asof",
            "as_of_system": as_of_before_dispatch,
        },
    )
    read_after_dispatch = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "read_asof",
            "as_of_system": as_of_after,
        },
    )
    missing_dispatch = execute_op(
        "view_render",
        {
            "document_id": "document:missing-batch6",
            "mode": "read_asof",
            "as_of_system": as_of_before_dispatch,
        },
    )

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_view",
        files_root=typed_files,
    )
    _seed_view_doc(typed_files)
    reg_t = typed_client.post(
        f"/views/{doc_id}/render",
        json={
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "agent": "cursor",
            "session_id": "batch6-session",
        },
    )
    assert reg_t.status_code == 200, reg_t.text
    as_of_before_typed = (reg_t.json().get("stamp") or {}).get("time") or as_of_before_dispatch
    typed_client.post(f"/views/{doc_id}/render", json={"mode": "full"})

    def _typed_read(as_of: str) -> dict[str, Any]:
        resp = typed_client.post(
            f"/views/{doc_id}/render",
            json={"mode": "read_asof", "as_of_system": as_of},
        )
        assert resp.status_code == 200, resp.text
        return _normalize_view_body(resp.json())

    read_before_typed = _typed_read(as_of_before_typed)
    read_after_typed = _typed_read(as_of_after)
    assert _normalize_view_body(read_before_dispatch) == read_before_typed
    assert _normalize_view_body(read_after_dispatch) == read_after_typed

    miss_resp = typed_client.post(
        "/views/document:missing-batch6/render",
        json={"mode": "read_asof", "as_of_system": as_of_before_typed},
    )
    assert miss_resp.status_code == 200
    assert _without_dispatch_envelope(miss_resp.json()) == _without_dispatch_envelope(
        missing_dispatch
    )
    assert dispatch_files.resolve() != _LIVE_FILES_ROOT.resolve()
    assert typed_files.resolve() != _LIVE_FILES_ROOT.resolve()


@pytest.mark.offline
def test_view_render_register_failure_parity_after_relationship(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register mode: derived_from then render — fail entity update (ops_views.py:300-309)."""
    dispatch_files = tmp_path / "files_dispatch_view_fail"
    typed_files = tmp_path / "files_typed_view_fail"
    dispatch_files.mkdir()
    typed_files.mkdir()

    def failing_update(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("injected entity update failure")

    monkeypatch.setattr(ops_views_mod, "update_entity_impl", failing_update)

    bind_db = tmp_path / "cortex_dispatch_view_reg_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_view_files(monkeypatch, dispatch_files)
    root_id, doc_id = _seed_view_doc(dispatch_files)
    reg_payload = {
        "document_id": doc_id,
        "mode": "register",
        "root_id": root_id,
        "view_profile": "matter_charter",
        "narrative_sections": {
            "narrative_layer": "Grounded [assertion:1] for batch6.",
        },
    }
    dispatch_raw = execute_op("view_render", reg_payload)
    assert dispatch_raw.get("code") == "view_not_registered"
    dispatch_rels = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM relationships WHERE from_entity = ?",
        (doc_id,),
    ).fetchone()[0]

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_view_reg_fail",
        files_root=typed_files,
    )
    _seed_view_doc(typed_files)
    body = {k: v for k, v in reg_payload.items() if k != "document_id"}
    resp = typed_client.post(f"/views/{doc_id}/render", json=body)
    assert resp.status_code == 200, resp.text
    typed_rels = cortex_db.cortex_conn().execute(
        "SELECT COUNT(*) FROM relationships WHERE from_entity = ?",
        (doc_id,),
    ).fetchone()[0]
    assert int(typed_rels) == int(dispatch_rels)


@pytest.mark.offline
def test_batch6_router_resolution_no_shadow_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_baseline_route_resolution(app)
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/strategy-rows")
        == "endeavor_write_row_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/strategy-rows/dispose")
        == "endeavor_dispose_row_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/views/document:probe/render")
        == "view_render_route"
    )
