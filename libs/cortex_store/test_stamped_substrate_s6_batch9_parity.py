"""S6 batch 9: dispatch vs typed parity for view_render."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._stamped_route_resolution_testkit import (
    assert_every_route_forward_matches_self,
    resolve_endpoint_name,
)
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store._write_lock_semantics_testkit import (
    assert_l3_lock_reacquirable,
    install_counting_write_lock,
)
from cortex_store import entity_crud
from cortex_store.claim_hash import compute_claim_hash
from cortex_store.conftest import bind_cortex_db
from cortex_store.db import query
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_views as views_mod
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
_LIVE_CORTEX_DB = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)
_LIVE_FILES_ROOT = shared_mod._FILES_ROOT
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

_DOC_ID = "document:batch9-view"
_ROOT_ID = "case:batch9-view"
_SOURCE_URI = "cortex://notes/views/batch9-view.md"
_NARRATIVE = {"narrative_layer": "Synthesis cites [assertion:1] for grounding."}
_FIXED_SNAPSHOT_AS_OF = "2026-01-01T00:00:00+00:00"


def _assert_isolated_db(db_path: Path) -> None:
    assert db_path.resolve() != _LIVE_CORTEX_DB.resolve()


def _bind_isolated_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    _assert_isolated_db(db_path)
    bind_cortex_db(monkeypatch, db_path)


def _freeze_view_snapshot_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """Volatile wall-clock ``as_of`` is embedded in rendered view files."""
    from cortex_store.dispatch_ops import ops_views
    from cortex_store.dispatch_ops._views import archive as archive_mod
    from cortex_store.dispatch_ops._views import render_core as rc

    original = rc.snapshot_for_scope

    def _frozen(
        conn: sqlite3.Connection, recipe: dict[str, Any], root_id: str | None = None
    ) -> dict[str, Any]:
        snap = original(conn, recipe, root_id)
        snap["as_of"] = _FIXED_SNAPSHOT_AS_OF
        return snap

    monkeypatch.setattr(rc, "snapshot_for_scope", _frozen)
    monkeypatch.setattr(ops_views, "snapshot_for_scope", _frozen)

    original_archive = archive_mod.archive_revision

    def _frozen_archive(
        files_root: Path,
        *,
        document_id: str,
        view_rev: int,
        body: str,
        archived_at: str | None = None,
    ) -> str:
        stamp = _FIXED_SNAPSHOT_AS_OF if view_rev <= 1 else "2026-02-01T00:00:00+00:00"
        return original_archive(
            files_root,
            document_id=document_id,
            view_rev=view_rev,
            body=body,
            archived_at=archived_at or stamp,
        )

    monkeypatch.setattr(archive_mod, "archive_revision", _frozen_archive)
    monkeypatch.setattr(ops_views, "archive_revision", _frozen_archive)


def _bind_files_root(monkeypatch: pytest.MonkeyPatch, files_root: Path) -> Path:
    files_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
    assert files_root.resolve() != _LIVE_FILES_ROOT.resolve()
    return files_root


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


def _files_inventory(files_root: Path) -> dict[str, str]:
    """Relative path → sha256 hex (files under isolated ``_FILES_ROOT``)."""
    inv: dict[str, str] = {}
    if not files_root.is_dir():
        return inv
    for path in sorted(files_root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(files_root))
            # Volatile: durable_write lock sidecars are not part of view_render parity.
            if rel.endswith(".lock") or "/.lock." in rel:
                continue
            body = path.read_bytes()
            inv[rel] = hashlib.sha256(body).hexdigest()
    return inv


def _full_view_inventory(
    conn: sqlite3.Connection,
    files_root: Path,
    document_id: str,
    root_id: str | None,
) -> dict[str, Any]:
    """Per-mode side-effect inventory (files, entities, relationships, events via caller)."""
    inv: dict[str, Any] = {}
    inv["files"] = _files_inventory(files_root)
    inv["entities"] = _query_table(conn, "entities", "id IN (?, ?)", (document_id, root_id or ""))
    if root_id:
        inv["relationships"] = _query_table(
            conn,
            "relationships",
            "(from_entity = ? AND to_entity = ?) OR (from_entity = ? AND to_entity = ?)",
            (document_id, root_id, root_id, document_id),
        )
    else:
        inv["relationships"] = _query_table(
            conn,
            "relationships",
            "from_entity = ? OR to_entity = ?",
            (document_id, document_id),
        )
    return inv


def _seed_view_entities(conn: sqlite3.Connection) -> tuple[str, str]:
    insert_entity(conn, entity_id=_ROOT_ID, entity_type="case", name=_ROOT_ID)
    insert_entity(conn, entity_id=_DOC_ID, entity_type="document", name=_DOC_ID)
    conn.execute(
        "UPDATE entities SET source_uri = ? WHERE id = ?",
        (_SOURCE_URI, _DOC_ID),
    )
    claim = f"claim for {_ROOT_ID}"
    claim_hash = compute_claim_hash(_ROOT_ID, claim)
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, evidence_uris) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, ?)",
        (_ROOT_ID, claim, claim_hash, json.dumps([_ROOT_ID])),
    )
    conn.commit()
    return _ROOT_ID, _DOC_ID


def _seed_registered_view(
    conn: sqlite3.Connection,
    files_root: Path,
    root_id: str,
    doc_id: str,
) -> None:
    _ = files_root  # caller must bind isolated ``_FILES_ROOT`` before register
    result = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    assert "error" not in result, result


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
) -> tuple[TestClient, Path, Path]:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    files_root = _bind_files_root(monkeypatch, tmp_path / f"files_{suffix}")
    client = TestClient(create_app(db_path=str(db_path)), raise_server_exceptions=False)
    return client, db_path, files_root


def _events_snapshot(events: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for signal, payload in events:
        if signal == "mcp.cortex.dispatch.shadow":
            continue
        p = dict(payload)
        p.pop("timestamp", None)
        out.append((signal, p))
    return out


def _capture_view_events(
    monkeypatch: pytest.MonkeyPatch,
    bucket: list[tuple[str, dict[str, Any]]],
) -> None:
    def _capture(signal: str, **payload: Any) -> None:
        bucket.append((signal, payload))

    monkeypatch.setattr(shared_mod, "record", _capture)

    def _pub(**payload: Any) -> None:
        bucket.append(("cortex.view.rendered", payload))

    monkeypatch.setattr(views_mod, "cortex_view_rendered", lambda **kw: _pub(**kw))


def _normalize_view_body(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_envelope(body)
    out = deepcopy(body)
    # Volatile: ``as_of`` wall-clock values are embedded in the rendered file body,
    # so ``written_sha256`` and ``stamp`` differ between sequential twin runs.
    out.pop("stamp", None)
    out.pop("written_sha256", None)
    return out


def _error_code(body: dict[str, Any]) -> str | None:
    if body.get("code"):
        return str(body["code"])
    err = body.get("error")
    if isinstance(err, dict):
        return str(err.get("code") or err.get("error"))
    return None


def _typed_post(
    client: TestClient, path: str, body: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    resp = client.post(path, json=body)
    return resp.json(), resp.status_code


def _run_parity_pair(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dispatch_call: Any,
    typed_call: Any,
    document_id: str,
    root_id: str | None,
    seed_fn: Any,
) -> None:
    _freeze_view_snapshot_as_of(monkeypatch)
    bind_db = tmp_path / "dispatch.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_dispatch")
    seed_fn(cortex_db.cortex_conn(), files_d)
    dispatch_events: list = []
    _capture_view_events(monkeypatch, dispatch_events)
    pre = _full_view_inventory(
        cortex_db.cortex_conn(), files_d, document_id, root_id
    )
    dispatch_raw = dispatch_call()
    post = _full_view_inventory(
        cortex_db.cortex_conn(), files_d, document_id, root_id
    )
    dispatch_body = _normalize_view_body(dispatch_raw)
    dispatch_snap = {"inventory": post, "pre": pre}
    dispatch_ev = _events_snapshot(dispatch_events)

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed"
    )
    seed_fn(cortex_db.cortex_conn(), files_t)
    typed_events: list = []
    _capture_view_events(monkeypatch, typed_events)
    pre_t = _full_view_inventory(
        cortex_db.cortex_conn(), files_t, document_id, root_id
    )
    typed_raw, typed_status = typed_call(client)
    post_t = _full_view_inventory(
        cortex_db.cortex_conn(), files_t, document_id, root_id
    )
    typed_body = _normalize_view_body(typed_raw)
    assert typed_body == dispatch_body, (dispatch_body, typed_body, typed_status)
    assert post_t["files"] == post["files"]
    assert post_t["relationships"] == post["relationships"]
    assert pre_t == pre
    assert _events_snapshot(typed_events) == dispatch_ev


@pytest.mark.offline
def test_view_render_register_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def seed(conn: sqlite3.Connection, _files: Path) -> None:
        _seed_view_entities(conn)

    _run_parity_pair(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        document_id=_DOC_ID,
        root_id=_ROOT_ID,
        seed_fn=seed,
        dispatch_call=lambda: execute_op(
            "view_render",
            {
                "document_id": _DOC_ID,
                "mode": "register",
                "root_id": _ROOT_ID,
                "view_profile": "matter_charter",
                "narrative_sections": _NARRATIVE,
            },
        ),
        typed_call=lambda c: _typed_post(
            c,
            f"/views/{_DOC_ID}/render",
            {
                "mode": "register",
                "root_id": _ROOT_ID,
                "view_profile": "matter_charter",
                "narrative_sections": _NARRATIVE,
            },
        ),
    )


@pytest.mark.offline
def _seed_refresh_delta(conn: sqlite3.Connection, files_root: Path) -> None:
    root_id, doc_id = _seed_view_entities(conn)
    _seed_registered_view(conn, files_root, root_id, doc_id)
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, evidence_uris, resolution_status) "
        "VALUES (?, 'new pending claim', 'believed', 'ev', 'inference', ?, ?, 'pending')",
        (
            _ROOT_ID,
            compute_claim_hash(_ROOT_ID, "new pending claim"),
            json.dumps([_ROOT_ID]),
        ),
    )
    row = conn.execute(
        "SELECT attributes FROM entities WHERE id = ?", (_DOC_ID,)
    ).fetchone()
    attrs = json.loads(row[0])
    attrs["derived_from_snapshot"]["max_assertion_id"] = 0
    conn.execute(
        "UPDATE entities SET attributes = ? WHERE id = ?",
        (json.dumps(attrs), _DOC_ID),
    )
    conn.commit()


def test_view_render_refresh_after_graph_change_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_view_snapshot_as_of(monkeypatch)

    def seed(conn: sqlite3.Connection, files_root: Path) -> None:
        _seed_refresh_delta(conn, files_root)

    _run_parity_pair(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        document_id=_DOC_ID,
        root_id=_ROOT_ID,
        seed_fn=seed,
        dispatch_call=lambda: execute_op(
            "view_render", {"document_id": _DOC_ID, "mode": "refresh"}
        ),
        typed_call=lambda c: _typed_post(
            c, f"/views/{_DOC_ID}/render", {"mode": "refresh"}
        ),
    )


@pytest.mark.offline
def test_view_render_full_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def seed(conn: sqlite3.Connection, files_root: Path) -> None:
        root_id, doc_id = _seed_view_entities(conn)
        _seed_registered_view(conn, files_root, root_id, doc_id)

    _run_parity_pair(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        document_id=_DOC_ID,
        root_id=_ROOT_ID,
        seed_fn=seed,
        dispatch_call=lambda: execute_op(
            "view_render", {"document_id": _DOC_ID, "mode": "full"}
        ),
        typed_call=lambda c: _typed_post(
            c, f"/views/{_DOC_ID}/render", {"mode": "full"}
        ),
    )


@pytest.mark.offline
def test_view_render_read_asof_two_timestamps_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_view_snapshot_as_of(monkeypatch)
    as_of_rev1 = _FIXED_SNAPSHOT_AS_OF
    as_of_rev2 = "2026-02-01T00:00:00+00:00"

    bind_db = tmp_path / "dispatch_asof.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_asof_d")
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    _seed_registered_view(cortex_db.cortex_conn(), files_d, root_id, doc_id)
    assert "error" not in execute_op("view_render", {"document_id": doc_id, "mode": "full"})
    head_path = files_d / "notes/views/batch9-view.md"
    from cortex_store.dispatch_ops._views.archive import archive_revision

    body_rev2 = head_path.read_text(encoding="utf-8").replace(
        _FIXED_SNAPSHOT_AS_OF, as_of_rev2
    )
    archive_revision(
        files_d,
        document_id=doc_id,
        view_rev=2,
        body=body_rev2,
        archived_at=as_of_rev2,
    )
    read_rev1_d = execute_op(
        "view_render",
        {"document_id": doc_id, "mode": "read_asof", "as_of_system": as_of_rev1},
    )
    assert "error" not in read_rev1_d, read_rev1_d
    read_rev2_d = execute_op(
        "view_render",
        {"document_id": doc_id, "mode": "read_asof", "as_of_system": as_of_rev2},
    )
    assert "error" not in read_rev2_d, read_rev2_d
    assert read_rev1_d.get("body") != read_rev2_d.get("body")

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="asof_t"
    )
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    _seed_registered_view(cortex_db.cortex_conn(), files_t, root_id, doc_id)
    assert client.post(f"/views/{doc_id}/render", json={"mode": "full"}).status_code == 200
    head_t = files_t / "notes/views/batch9-view.md"
    body_rev2_t = head_t.read_text(encoding="utf-8").replace(
        _FIXED_SNAPSHOT_AS_OF, as_of_rev2
    )
    archive_revision(
        files_t,
        document_id=doc_id,
        view_rev=2,
        body=body_rev2_t,
        archived_at=as_of_rev2,
    )
    read_rev1_t = client.post(
        f"/views/{doc_id}/render",
        json={"mode": "read_asof", "as_of_system": as_of_rev1},
    ).json()
    read_rev2_t = client.post(
        f"/views/{doc_id}/render",
        json={"mode": "read_asof", "as_of_system": as_of_rev2},
    ).json()
    assert _normalize_view_body(read_rev1_t) == _normalize_view_body(read_rev1_d)
    assert _normalize_view_body(read_rev2_t) == _normalize_view_body(read_rev2_d)


@pytest.mark.offline
def test_view_render_error_cases_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("missing", {"document_id": "document:missing", "mode": "register"}, "document_not_found"),
        (
            "not_document",
            {"document_id": _ROOT_ID, "mode": "register", "view_profile": "matter_index"},
            "document_not_found",
        ),
        ("invalid_mode", {"document_id": _DOC_ID, "mode": "not_a_mode"}, "unknown_view_profile"),
        (
            "as_of_valid",
            {"document_id": _DOC_ID, "as_of_valid": "2026-01-01T00:00:00Z"},
            "as_of_valid_unsupported",
        ),
        (
            "read_asof_no_system",
            {"document_id": _DOC_ID, "mode": "read_asof"},
            "as_of_instance_not_found",
        ),
    ]
    _freeze_view_snapshot_as_of(monkeypatch)
    for label, payload, code in cases:
        bind_db = tmp_path / f"dispatch_err_{label}.db"
        copy_template_db(migrated_db_template, bind_db)
        _bind_isolated_db(monkeypatch, bind_db)
        _bind_files_root(monkeypatch, tmp_path / f"files_err_d_{label}")
        if label == "not_document":
            _seed_view_entities(cortex_db.cortex_conn())
        elif label not in ("missing", "invalid_mode", "as_of_valid"):
            root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
            if label == "read_asof_no_system":
                _seed_registered_view(
                    cortex_db.cortex_conn(), tmp_path / f"files_err_d_{label}", root_id, doc_id
                )
        pre = _full_view_inventory(
            cortex_db.cortex_conn(),
            tmp_path / f"files_err_d_{label}",
            payload.get("document_id", _DOC_ID),
            _ROOT_ID,
        )
        dispatch_events: list = []
        _capture_view_events(monkeypatch, dispatch_events)
        dispatch_raw = execute_op("view_render", payload)
        assert _error_code(dispatch_raw) == code, (label, dispatch_raw)
        assert _events_snapshot(dispatch_events) == []

        client, _, files_t = _isolated_client(
            migrated_db_template, tmp_path, monkeypatch, suffix=f"err_t_{label}"
        )
        if label == "not_document":
            _seed_view_entities(cortex_db.cortex_conn())
        elif label not in ("missing", "invalid_mode", "as_of_valid"):
            root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
            if label == "read_asof_no_system":
                _seed_registered_view(cortex_db.cortex_conn(), files_t, root_id, doc_id)
        pre_t = _full_view_inventory(
            cortex_db.cortex_conn(),
            files_t,
            payload.get("document_id", _DOC_ID),
            _ROOT_ID,
        )
        doc_path = payload.get("document_id", _DOC_ID)
        body = {k: v for k, v in payload.items() if k != "document_id"}
        typed_events: list = []
        _capture_view_events(monkeypatch, typed_events)
        resp = client.post(f"/views/{doc_path}/render", json=body)
        assert resp.status_code == 200, resp.text
        assert _error_code(resp.json()) == code
        assert _full_view_inventory(
            cortex_db.cortex_conn(), files_t, doc_path, _ROOT_ID
        ) == pre_t
        assert pre_t == pre


@pytest.mark.offline
def test_view_render_s4_narrative_sections_not_object(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known S4: dispatch accepts/coerces vs typed 422; DB unchanged."""
    _freeze_view_snapshot_as_of(monkeypatch)
    bind_db = tmp_path / "dispatch_s4.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _bind_files_root(monkeypatch, tmp_path / "files_s4_d")
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    pre = _full_view_inventory(cortex_db.cortex_conn(), tmp_path / "files_s4_d", doc_id, root_id)
    with pytest.raises(TypeError):
        execute_op(
            "view_render",
            {
                "document_id": doc_id,
                "mode": "register",
                "root_id": root_id,
                "view_profile": "matter_charter",
                "narrative_sections": "not-an-object",
            },
        )
    post = _full_view_inventory(
        cortex_db.cortex_conn(), tmp_path / "files_s4_d", doc_id, root_id
    )
    assert post["files"] == pre["files"]
    assert post["entities"] == pre["entities"]

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="s4_t"
    )
    _seed_view_entities(cortex_db.cortex_conn())
    pre_t = _full_view_inventory(cortex_db.cortex_conn(), files_t, doc_id, root_id)
    resp = client.post(
        f"/views/{doc_id}/render",
        json={
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": "not-an-object",
        },
    )
    assert resp.status_code == 422
    assert _full_view_inventory(cortex_db.cortex_conn(), files_t, doc_id, root_id) == pre_t


@pytest.mark.offline
def test_view_render_register_failure_compensation_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject at first ``update_entity_impl`` after file write (ops_views.py:300)."""
    _freeze_view_snapshot_as_of(monkeypatch)
    original_update = entity_crud.update_entity_impl
    def fail_register_update(*args: Any, **kwargs: Any) -> Any:
        entity_id = kwargs.get("entity_id")
        if entity_id is None and len(args) >= 2:
            entity_id = args[1]
        updates = kwargs.get("updates")
        if updates is None and len(args) >= 3:
            updates = args[2]
        if entity_id == doc_id and isinstance(updates, dict) and "attributes" in updates:
            raise RuntimeError("injected after derived_from and file write")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(entity_crud, "update_entity_impl", fail_register_update)
    monkeypatch.setattr(views_mod, "update_entity_impl", fail_register_update)
    rollback_calls: list[int | None] = []
    original_rollback = views_mod._rollback_relationship

    def _spy_rollback(rel_id: int | None) -> None:
        rollback_calls.append(rel_id)
        original_rollback(rel_id)

    monkeypatch.setattr(views_mod, "_rollback_relationship", _spy_rollback)

    bind_db = tmp_path / "dispatch_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_fail_d")
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    pre = _full_view_inventory(cortex_db.cortex_conn(), files_d, doc_id, root_id)
    dispatch_events: list = []
    _capture_view_events(monkeypatch, dispatch_events)
    dispatch_raw = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    assert dispatch_raw.get("code") == "view_not_registered"
    assert _files_inventory(files_d) == {}
    assert _events_snapshot(dispatch_events) == []
    rels = _query_table(
        cortex_db.cortex_conn(),
        "relationships",
        "from_entity = ? AND to_entity = ? AND type = 'derived_from' AND active = 1",
        (doc_id, root_id),
    )
    assert rollback_calls, "expected _rollback_relationship after update_entity_impl failure"
    assert rels == [], f"active derived_from remains after rollback: {rels}"
    doc_row = _query_table(
        cortex_db.cortex_conn(), "entities", "id = ?", (doc_id,)
    )[0]
    assert doc_row.get("attributes") in (None, "null", pre["entities"][1].get("attributes"))
    assert doc_row.get("content_hash") in (None, pre["entities"][1].get("content_hash"))

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="fail_t"
    )
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    pre_t = _full_view_inventory(cortex_db.cortex_conn(), files_t, doc_id, root_id)
    counter = install_counting_write_lock(monkeypatch, views_mod)
    typed_events: list = []
    _capture_view_events(monkeypatch, typed_events)
    resp = client.post(
        f"/views/{doc_id}/render",
        json={
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    if resp.status_code == 200:
        assert resp.json().get("code") == "view_not_registered"
    else:
        assert resp.status_code == 500
    assert _files_inventory(files_t) == {}
    doc_t = _query_table(cortex_db.cortex_conn(), "entities", "id = ?", (doc_id,))[0]
    assert doc_t.get("attributes") in (None, "null", pre_t["entities"][1].get("attributes"))
    assert _events_snapshot(typed_events) == []
    assert_l3_lock_reacquirable(counter)


@pytest.mark.offline
def test_batch9_router_resolution_full_walk_no_skip(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    _assert_isolated_db(migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_every_route_forward_matches_self(app, skip_endpoint_names=frozenset())
    assert (
        resolve_endpoint_name(app, "POST", "/views/document:probe/render")
        == "view_render_route"
    )
