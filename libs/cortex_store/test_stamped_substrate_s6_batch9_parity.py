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
_REFRESH_BODY = {"mode": "refresh", "root_id": _ROOT_ID}


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
    monkeypatch.setattr(views_mod, "record", _capture)

    def _pub(**payload: Any) -> None:
        bucket.append(("cortex.view.rendered", payload))

    monkeypatch.setattr(views_mod, "cortex_view_rendered", lambda **kw: _pub(**kw))


def _normalize_view_body(body: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(_without_envelope(body))


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


def _typed_post_bodiless(
    client: TestClient, path: str
) -> tuple[dict[str, Any], int]:
    resp = client.post(path)
    return resp.json(), resp.status_code


def _doc_entity_row(conn: sqlite3.Connection, document_id: str) -> dict[str, Any]:
    rows = _query_table(conn, "entities", "id = ?", (document_id,))
    assert len(rows) == 1, rows
    return rows[0]


def _insert_active_derived_from(
    conn: sqlite3.Connection, document_id: str, root_id: str
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        ("derived_from", "fixture"),
    )
    cur = conn.execute(
        "INSERT INTO relationships "
        "(from_entity, to_entity, type, active, strength, created_at, updated_at) "
        "VALUES (?, ?, 'derived_from', 1, 1.0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (document_id, root_id),
    )
    conn.commit()
    return int(cur.lastrowid)


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
    assert_post_differs_from_pre: bool = False,
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
    dispatch_ev = _events_snapshot(dispatch_events)
    if assert_post_differs_from_pre:
        assert post["files"] != pre["files"] or post["entities"] != pre["entities"], (
            pre,
            post,
        )
        pre_doc = next(r for r in pre["entities"] if r.get("id") == document_id)
        post_doc = next(r for r in post["entities"] if r.get("id") == document_id)
        assert post_doc != pre_doc, (pre_doc, post_doc)

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
    assert post_t["entities"] == post["entities"]
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


def _insert_pending_assertion(conn: sqlite3.Connection, root_id: str, claim: str) -> None:
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, evidence_uris, resolution_status) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, ?, 'pending')",
        (
            root_id,
            claim,
            compute_claim_hash(root_id, claim),
            json.dumps([root_id]),
        ),
    )


def _lower_max_assertion_id(conn: sqlite3.Connection, doc_id: str) -> None:
    row = conn.execute(
        "SELECT attributes FROM entities WHERE id = ?", (doc_id,)
    ).fetchone()
    attrs = json.loads(row[0])
    attrs["derived_from_snapshot"]["max_assertion_id"] = 0
    conn.execute(
        "UPDATE entities SET attributes = ? WHERE id = ?",
        (json.dumps(attrs), doc_id),
    )


def _seed_pending_refresh_delta(
    conn: sqlite3.Connection, files_root: Path
) -> tuple[str, str]:
    root_id, doc_id = _seed_view_entities(conn)
    _seed_registered_view(conn, files_root, root_id, doc_id)
    _insert_pending_assertion(conn, root_id, "new pending claim for refresh")
    _lower_max_assertion_id(conn, doc_id)
    conn.commit()
    return root_id, doc_id


def _seed_refresh_delta(conn: sqlite3.Connection, files_root: Path) -> None:
    _seed_pending_refresh_delta(conn, files_root)
    result = execute_op(
        "view_render", {"document_id": _DOC_ID, **_REFRESH_BODY}
    )
    assert "error" not in result, result


@pytest.mark.offline
def test_view_render_refresh_after_graph_change_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_view_snapshot_as_of(monkeypatch)

    def seed(conn: sqlite3.Connection, files_root: Path) -> None:
        _seed_pending_refresh_delta(conn, files_root)

    _run_parity_pair(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        document_id=_DOC_ID,
        root_id=_ROOT_ID,
        seed_fn=seed,
        assert_post_differs_from_pre=True,
        dispatch_call=lambda: execute_op(
            "view_render", {"document_id": _DOC_ID, **_REFRESH_BODY}
        ),
        typed_call=lambda c: _typed_post(
            c, f"/views/{_DOC_ID}/render", _REFRESH_BODY
        ),
    )


@pytest.mark.offline
def test_view_render_bodiless_post_refresh_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bodiless typed POST defaults to refresh like /dispatch with only document_id."""

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
        dispatch_call=lambda: execute_op("view_render", {"document_id": _DOC_ID}),
        typed_call=lambda c: _typed_post_bodiless(c, f"/views/{_DOC_ID}/render"),
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


def _archive_revision_path(files_root: Path, doc_id: str, view_rev: int) -> Path:
    slug = doc_id.split(":", 1)[-1]
    path = files_root / f"notes/system/views/revisions/{slug}/rev-{view_rev}.md"
    assert path.is_file(), path
    return path


def _archive_as_of_system(files_root: Path, doc_id: str, view_rev: int) -> str:
    """``read_asof_instance`` keys off stamp ``time`` inside the archived body."""
    from cortex_store.dispatch_ops._views.archive import _stamp_time

    text = _archive_revision_path(files_root, doc_id, view_rev).read_text(encoding="utf-8")
    if text.startswith("<!--"):
        text = text.split("\n", 1)[1]
    stamp = _stamp_time(text)
    assert stamp, view_rev
    return stamp


def _read_asof_pair(
    conn: sqlite3.Connection,
    files_root: Path,
    *,
    via_client: TestClient | None,
    doc_id: str,
) -> tuple[str, str]:
    """Two refreshes with graph changes between; return read_asof bodies at rev1/rev2 stamps."""
    _seed_pending_refresh_delta(conn, files_root)
    refresh = {"document_id": doc_id, **_REFRESH_BODY}
    if via_client is None:
        assert "error" not in execute_op("view_render", refresh)
        as_of_rev1 = _archive_as_of_system(files_root, doc_id, 1)
        _insert_pending_assertion(conn, _ROOT_ID, "second pending claim for asof")
        _lower_max_assertion_id(conn, doc_id)
        conn.commit()
        assert "error" not in execute_op("view_render", refresh)
        as_of_rev2 = _archive_as_of_system(files_root, doc_id, 2)
        read_rev1 = execute_op(
            "view_render",
            {"document_id": doc_id, "mode": "read_asof", "as_of_system": as_of_rev1},
        )
        read_rev2 = execute_op(
            "view_render",
            {"document_id": doc_id, "mode": "read_asof", "as_of_system": as_of_rev2},
        )
    else:
        assert via_client.post(f"/views/{doc_id}/render", json=_REFRESH_BODY).status_code == 200
        as_of_rev1 = _archive_as_of_system(files_root, doc_id, 1)
        _insert_pending_assertion(conn, _ROOT_ID, "second pending claim for asof")
        _lower_max_assertion_id(conn, doc_id)
        conn.commit()
        assert via_client.post(f"/views/{doc_id}/render", json=_REFRESH_BODY).status_code == 200
        as_of_rev2 = _archive_as_of_system(files_root, doc_id, 2)
        read_rev1 = via_client.post(
            f"/views/{doc_id}/render",
            json={"mode": "read_asof", "as_of_system": as_of_rev1},
        ).json()
        read_rev2 = via_client.post(
            f"/views/{doc_id}/render",
            json={"mode": "read_asof", "as_of_system": as_of_rev2},
        ).json()
    assert "error" not in read_rev1, read_rev1
    assert "error" not in read_rev2, read_rev2
    body1 = read_rev1.get("body") or ""
    body2 = read_rev2.get("body") or ""
    assert as_of_rev1 != as_of_rev2
    assert body1 != body2, "archived instances must differ after graph-driven refreshes"
    assert "new pending claim for refresh" in body2
    assert "new pending claim for refresh" not in body1
    return body1, body2


def _freeze_build_stamp_time_by_rev(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_store.dispatch_ops import ops_views
    from cortex_store.dispatch_ops._views import stamps as stamps_mod

    original = stamps_mod.build_stamp

    def _stamped(**kwargs: Any) -> dict[str, Any]:
        stamp = original(**kwargs)
        if int(kwargs.get("view_rev") or 0) >= 2:
            stamp["time"] = "2026-02-01T00:00:00+00:00"
        return stamp

    monkeypatch.setattr(stamps_mod, "build_stamp", _stamped)
    monkeypatch.setattr(ops_views, "build_stamp", _stamped)


@pytest.mark.offline
def test_view_render_read_asof_two_timestamps_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_view_snapshot_as_of(monkeypatch)
    _freeze_build_stamp_time_by_rev(monkeypatch)
    bind_db = tmp_path / "dispatch_asof.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_asof_d")
    body1_d, body2_d = _read_asof_pair(
        cortex_db.cortex_conn(),
        files_d,
        via_client=None,
        doc_id=_DOC_ID,
    )

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="asof_t"
    )
    body1_t, body2_t = _read_asof_pair(
        cortex_db.cortex_conn(),
        files_t,
        via_client=client,
        doc_id=_DOC_ID,
    )
    assert body1_t == body1_d
    assert body2_t == body2_d


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


def _active_derived_from_edges(
    conn: sqlite3.Connection, document_id: str, root_id: str
) -> list[dict[str, Any]]:
    return _query_table(
        conn,
        "relationships",
        "from_entity = ? AND to_entity = ? AND type = 'derived_from' AND active = 1",
        (document_id, root_id),
    )


def _assert_view_render_reject_clean_state(
    conn: sqlite3.Connection,
    files_root: Path,
    document_id: str,
    root_id: str,
    pre: dict[str, Any],
    post: dict[str, Any],
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    assert post == pre
    assert _active_derived_from_edges(conn, document_id, root_id) == []
    assert _events_snapshot(events) == []


@pytest.mark.offline
def test_view_render_s4_narrative_sections_not_object(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Known S4 (4a2e0268bf10172f): typed 422 vs dispatch 200+error; identical stored state."""
    _freeze_view_snapshot_as_of(monkeypatch)
    payload = {
        "document_id": _DOC_ID,
        "mode": "register",
        "root_id": _ROOT_ID,
        "view_profile": "matter_charter",
        "narrative_sections": "not-an-object",
    }
    bind_db = tmp_path / "dispatch_s4.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_s4_d")
    _seed_view_entities(cortex_db.cortex_conn())
    pre = _full_view_inventory(cortex_db.cortex_conn(), files_d, _DOC_ID, _ROOT_ID)
    assert _active_derived_from_edges(
        cortex_db.cortex_conn(), _DOC_ID, _ROOT_ID
    ) == []
    dispatch_events: list = []
    _capture_view_events(monkeypatch, dispatch_events)
    dispatch_raw = execute_op("view_render", payload)
    assert _error_code(dispatch_raw) == "invalid_narrative_sections", dispatch_raw
    post = _full_view_inventory(cortex_db.cortex_conn(), files_d, _DOC_ID, _ROOT_ID)
    _assert_view_render_reject_clean_state(
        cortex_db.cortex_conn(),
        files_d,
        _DOC_ID,
        _ROOT_ID,
        pre,
        post,
        dispatch_events,
    )

    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="s4_t"
    )
    _seed_view_entities(cortex_db.cortex_conn())
    pre_t = _full_view_inventory(cortex_db.cortex_conn(), files_t, _DOC_ID, _ROOT_ID)
    typed_events: list = []
    _capture_view_events(monkeypatch, typed_events)
    resp = client.post(
        f"/views/{_DOC_ID}/render",
        json={
            "mode": "register",
            "root_id": _ROOT_ID,
            "view_profile": "matter_charter",
            "narrative_sections": "not-an-object",
        },
    )
    assert resp.status_code == 422, resp.text
    post_t = _full_view_inventory(cortex_db.cortex_conn(), files_t, _DOC_ID, _ROOT_ID)
    _assert_view_render_reject_clean_state(
        cortex_db.cortex_conn(),
        files_t,
        _DOC_ID,
        _ROOT_ID,
        pre_t,
        post_t,
        typed_events,
    )


def _install_register_update_failure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    doc_id: str,
    rollback_calls: list[int | None],
) -> None:
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
    original_rollback = views_mod._rollback_relationship

    def _spy_rollback(rel_id: int | None) -> None:
        rollback_calls.append(rel_id)
        original_rollback(rel_id)

    monkeypatch.setattr(views_mod, "_rollback_relationship", _spy_rollback)


@pytest.mark.offline
def test_view_render_register_failure_compensation_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject at register ``update_entity_impl`` (ops_views.py:304)."""
    _freeze_view_snapshot_as_of(monkeypatch)
    register_payload = {
        "document_id": _DOC_ID,
        "mode": "register",
        "root_id": _ROOT_ID,
        "view_profile": "matter_charter",
        "narrative_sections": _NARRATIVE,
    }

    rollback_d: list[int | None] = []
    _install_register_update_failure(monkeypatch, doc_id=_DOC_ID, rollback_calls=rollback_d)
    bind_db = tmp_path / "dispatch_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    files_d = _bind_files_root(monkeypatch, tmp_path / "files_fail_d")
    _seed_view_entities(cortex_db.cortex_conn())
    pre_doc = _doc_entity_row(cortex_db.cortex_conn(), _DOC_ID)
    dispatch_events: list = []
    _capture_view_events(monkeypatch, dispatch_events)
    counter_d = install_counting_write_lock(monkeypatch, views_mod)
    dispatch_raw = execute_op("view_render", register_payload)
    assert dispatch_raw.get("code") == "view_not_registered"
    assert _files_inventory(files_d) == {}
    assert _events_snapshot(dispatch_events) == []
    rels_d = _query_table(
        cortex_db.cortex_conn(),
        "relationships",
        "from_entity = ? AND to_entity = ? AND type = 'derived_from' AND active = 1",
        (_DOC_ID, _ROOT_ID),
    )
    assert rollback_d and rollback_d[-1] is not None
    assert rels_d == [], f"active derived_from remains after rollback: {rels_d}"
    assert _doc_entity_row(cortex_db.cortex_conn(), _DOC_ID) == pre_doc
    assert_l3_lock_reacquirable(counter_d)

    rollback_t: list[int | None] = []
    _install_register_update_failure(monkeypatch, doc_id=_DOC_ID, rollback_calls=rollback_t)
    client, _, files_t = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="fail_t"
    )
    _seed_view_entities(cortex_db.cortex_conn())
    pre_doc_t = _doc_entity_row(cortex_db.cortex_conn(), _DOC_ID)
    counter_t = install_counting_write_lock(monkeypatch, views_mod)
    typed_events: list = []
    _capture_view_events(monkeypatch, typed_events)
    resp = client.post(
        f"/views/{_DOC_ID}/render",
        json={
            "mode": "register",
            "root_id": _ROOT_ID,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("code") == "view_not_registered"
    assert _files_inventory(files_t) == {}
    rels_t = _query_table(
        cortex_db.cortex_conn(),
        "relationships",
        "from_entity = ? AND to_entity = ? AND type = 'derived_from' AND active = 1",
        (_DOC_ID, _ROOT_ID),
    )
    assert rollback_t and rollback_t[-1] is not None
    assert rels_t == []
    assert _doc_entity_row(cortex_db.cortex_conn(), _DOC_ID) == pre_doc_t
    assert _events_snapshot(typed_events) == []
    assert_l3_lock_reacquirable(counter_t)


@pytest.mark.offline
def test_view_render_register_failure_preserves_preexisting_derived_from(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B1: deduped derived_from must survive register failure (both paths)."""
    _freeze_view_snapshot_as_of(monkeypatch)
    edge_id = None

    def _seed_with_edge(conn: sqlite3.Connection) -> None:
        nonlocal edge_id
        _seed_view_entities(conn)
        edge_id = _insert_active_derived_from(conn, _DOC_ID, _ROOT_ID)

    rollback_d: list[int | None] = []
    _install_register_update_failure(monkeypatch, doc_id=_DOC_ID, rollback_calls=rollback_d)
    bind_db = tmp_path / "dispatch_edge.db"
    copy_template_db(migrated_db_template, bind_db)
    _bind_isolated_db(monkeypatch, bind_db)
    _bind_files_root(monkeypatch, tmp_path / "files_edge_d")
    _seed_with_edge(cortex_db.cortex_conn())
    result = execute_op(
        "view_render",
        {
            "document_id": _DOC_ID,
            "mode": "register",
            "root_id": _ROOT_ID,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    assert result.get("code") == "view_not_registered"
    rels = _query_table(
        cortex_db.cortex_conn(),
        "relationships",
        "id = ? AND active = 1",
        (edge_id,),
    )
    assert len(rels) == 1
    assert rollback_d == [] or all(r is None for r in rollback_d)

    rollback_t: list[int | None] = []
    _install_register_update_failure(monkeypatch, doc_id=_DOC_ID, rollback_calls=rollback_t)
    client, _, _ = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="edge_t"
    )
    _seed_with_edge(cortex_db.cortex_conn())
    resp = client.post(
        f"/views/{_DOC_ID}/render",
        json={
            "mode": "register",
            "root_id": _ROOT_ID,
            "view_profile": "matter_charter",
            "narrative_sections": _NARRATIVE,
        },
    )
    assert resp.status_code == 200
    assert resp.json().get("code") == "view_not_registered"
    rels_t = _query_table(
        cortex_db.cortex_conn(),
        "relationships",
        "id = ? AND active = 1",
        (edge_id,),
    )
    assert len(rels_t) == 1


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
