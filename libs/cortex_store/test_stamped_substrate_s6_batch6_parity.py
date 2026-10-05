"""S6 batch 6: dispatch vs typed parity for endeavor strategy rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._stamped_route_resolution_testkit import (
    assert_baseline_route_resolution,
    assert_pre_batch6_route_resolution_unchanged,
    resolve_endpoint_name,
)
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import decode_row, json_encode, query
from cortex_store.dispatch_ops import execute_op
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
# Nested assertion payloads: ids and wall-clock fields differ run-to-run
_VOLATILE_ASSERTION_KEYS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "observed_at",
        "valid_from",  # supersede sets from `now` at second resolution
        "valid_until",
    }
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
    for key in (
        "assertion_id",  # runtime id — compare table readback instead
        "pin",
        "superseded_assertion_id",
        "disposing_assertion_id",
    ):
        if key in body:
            body = dict(body)
            body.pop(key, None)
    return body


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    assert db_path.parent == tmp_path
    bind_cortex_db(monkeypatch, db_path)
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
            "bounds": "batch6-bounds",
            "visibility": "internal",
            "pin": 99999,
        },
    }


def _assertion_id_ordinals(conn: sqlite3.Connection, host: str) -> dict[int, str]:
    rows = query(
        conn,
        "SELECT id FROM assertions WHERE entity_id = ? ORDER BY id",
        (host,),
    )
    return {int(row["id"]): f"a{idx}" for idx, row in enumerate(rows, start=1)}


def _normalize_assertion_node(node: str, id_ord: dict[int, str]) -> str:
    if node.startswith("assertion:"):
        try:
            raw = int(node.split(":", 1)[1])
        except ValueError:
            return node
        return f"assertion:{id_ord.get(raw, raw)}"
    return node


def _assertion_rows_for_host(conn: sqlite3.Connection, host: str) -> list[dict[str, Any]]:
    rows = query(
        conn,
        "SELECT * FROM assertions WHERE entity_id = ? ORDER BY id",
        (host,),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(decode_row(row, {"attributes", "evidence_uris"}))
        item.pop("id", None)  # volatile: assigned at insert
        item.pop("created_at", None)
        item.pop("updated_at", None)
        item.pop("observed_at", None)
        item.pop("valid_from", None)  # volatile: supersede timestamps
        item.pop("valid_until", None)
        attrs = item.get("attributes")
        if isinstance(attrs, dict):
            attrs = dict(attrs)
            attrs.pop("pin", None)  # volatile: handler assigns pin to new assertion id
            item["attributes"] = attrs
        out.append(item)
    return out


def _session_edges_snapshot(conn: sqlite3.Connection, host: str) -> list[dict[str, Any]]:
    id_ord = _assertion_id_ordinals(conn, host)
    rows = query(
        conn,
        "SELECT session_id, agent, from_node, to_node, edge_type, strength, edge_source, context "
        "FROM session_edges ORDER BY id",
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.pop("session_id", None)  # volatile: supersede stamps unknown-<iso>-<suffix>
        item["from_node"] = _normalize_assertion_node(str(item["from_node"]), id_ord)
        item["to_node"] = _normalize_assertion_node(str(item["to_node"]), id_ord)
        out.append(item)
    return out


def _endeavor_db_snapshot(conn: sqlite3.Connection, host: str) -> tuple[list, list]:
    return _assertion_rows_for_host(conn, host), _session_edges_snapshot(conn, host)


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
    assert bind_db.parent == tmp_path
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    dispatch_raw = execute_op("endeavor_write_row", payload)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_endeavor_body(dispatch_raw)
    dispatch_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write_row"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    resp = typed_client.post("/endeavors/strategy-rows", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_endeavor_body(resp.json())
    assert dispatch_body == typed_body
    typed_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)
    assert typed_snap == dispatch_snap


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
    assert bind_db.parent == tmp_path
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    execute_op("endeavor_write_row", write_payload)
    dispatch_raw = execute_op("endeavor_dispose_row", dispose_payload)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_endeavor_body(dispatch_raw)
    dispatch_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_dispose"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    typed_client.post("/endeavors/strategy-rows", json=write_payload)
    resp = typed_client.post("/endeavors/strategy-rows/dispose", json=dispose_payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_endeavor_body(resp.json())
    assert dispatch_body == typed_body
    typed_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)
    assert typed_snap == dispatch_snap


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
    dispatch_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write_fail"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    resp = typed_client.post("/endeavors/strategy-rows", json=payload)
    assert resp.status_code == 200, resp.text
    assert "error" in resp.json()
    typed_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)
    assert typed_snap == dispatch_snap


@pytest.mark.offline
def test_endeavor_dispose_row_failure_parity_supersede_surface(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure after new assertion INSERT, before CAS UPDATE (_supersede.py:359-417)."""
    host = "opportunity:batch6-dispose-fail-host"
    write_payload = _write_row_payload(host)
    write_payload["fields"]["row_id"] = "batch6-sup-fail-r1"
    dispose_payload = {
        "host": host,
        "row_id": "batch6-sup-fail-r1",
        "disposition": "express",
        "authority": "agent:batch6",
    }

    import cortex_store.db as db_mod

    state = {"new_inserted": False}
    orig_cortex_conn = db_mod.cortex_conn

    class _GuardConn:
        __slots__ = ("_inner",)

        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
            if isinstance(sql, str) and "INSERT INTO assertions" in sql:
                state["new_inserted"] = True
                return self._inner.execute(sql, params)
            if (
                state["new_inserted"]
                and isinstance(sql, str)
                and "UPDATE assertions SET valid_until" in sql
            ):
                raise sqlite3.OperationalError("injected between insert and cas update")
            return self._inner.execute(sql, params)

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        def __enter__(self) -> _GuardConn:
            return self

        def __exit__(self, *exc: object) -> None:
            self._inner.close()

    def guarded_cortex_conn() -> _GuardConn:
        return _GuardConn(orig_cortex_conn())

    import cortex_store.routes.assertions._supersede as supersede_mod

    monkeypatch.setattr(supersede_mod, "cortex_conn", guarded_cortex_conn)

    bind_db = tmp_path / "cortex_dispatch_dispose_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    execute_op("endeavor_write_row", write_payload)
    dispatch_raw = execute_op("endeavor_dispose_row", dispose_payload)
    assert "error" in dispatch_raw
    dispatch_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_dispose_fail"
    )
    _insert_endeavor_host(cortex_db.cortex_conn(), host)
    typed_client.post("/endeavors/strategy-rows", json=write_payload)
    resp = typed_client.post("/endeavors/strategy-rows/dispose", json=dispose_payload)
    assert resp.status_code == 200, resp.text
    assert "error" in resp.json()
    typed_snap = _endeavor_db_snapshot(cortex_db.cortex_conn(), host)
    assert typed_snap == dispatch_snap


@pytest.mark.offline
def test_batch6_router_resolution_no_shadow_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_pre_batch6_route_resolution_unchanged(app)
    assert_baseline_route_resolution(app)
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/strategy-rows")
        == "endeavor_write_row_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/endeavors/strategy-rows/dispose")
        == "endeavor_dispose_row_route"
    )
