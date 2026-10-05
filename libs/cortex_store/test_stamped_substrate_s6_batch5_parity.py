"""S6 batch 5: dispatch vs typed parity for sidecar and pinned deliverable writes."""

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
from cortex_store.db import decode_row, query
from cortex_store.dispatch_ops import _pinned_deliverable as pinned_mod
from cortex_store.dispatch_ops import _recon_sidecar as recon_mod
from cortex_store.dispatch_ops import _shared as shared_mod
from cortex_store.dispatch_ops import _thread_sidecar as thread_mod
from cortex_store.dispatch_ops import _todo_closure_sidecar as todo_closure_mod
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_todos as ops_todos_mod
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
# path: absolute filesystem path under tmp — not comparable across twin roots
_VOLATILE_RESPONSE_KEYS = frozenset({"path"})
# File bytes: strip volatile frontmatter timestamps only (recon created_at, thread written_at).
# Do not strip todo **Closed:** — fixture pins closed_at so that line is part of parity.
_TIMESTAMP_LINE_RE = re.compile(
    r"^(created_at|written_at):\s*.*$",
    re.MULTILINE,
)
_LIVE_FILES_ROOT = shared_mod._FILES_ROOT


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _normalize_response(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    return {k: v for k, v in body.items() if k not in _VOLATILE_RESPONSE_KEYS}


def _normalize_file_bytes(rel: str, data: bytes) -> bytes:
    text = data.decode("utf-8")
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if not _TIMESTAMP_LINE_RE.match(line.rstrip("\n"))
    ]
    return "".join(lines).encode("utf-8")


def _file_tree(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "/.lock." in rel or rel.startswith(".lock."):
            continue
        out[rel] = _normalize_file_bytes(rel, path.read_bytes())
    return out


def _entity_attributes(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    rows = query(conn, "SELECT attributes FROM entities WHERE id = ?", (entity_id,))
    if not rows:
        return {}
    data = decode_row(rows[0], ENTITY_JSON_FIELDS)
    attrs = data.get("attributes")
    return dict(attrs) if isinstance(attrs, dict) else {}


def _bind_isolated_files(monkeypatch: pytest.MonkeyPatch, files_root: Path) -> None:
    for mod in (recon_mod, thread_mod, todo_closure_mod, pinned_mod, shared_mod):
        monkeypatch.setattr(mod, "_FILES_ROOT", files_root)


def _isolated_client(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    files_root: Path,
) -> TestClient:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    _bind_isolated_files(monkeypatch, files_root)
    return TestClient(create_app(db_path=str(db_path)))


def _assert_not_live_files_root(files_root: Path) -> None:
    assert files_root.resolve() != _LIVE_FILES_ROOT.resolve()


def _recon_payload() -> dict[str, Any]:
    return {
        "label": "thread-batch5",
        "theme": "sidecar-stamp",
        "body": "# Theme: sidecar\n\nBatch 5 parity body.",
        "scopes": ["research", "dispatch"],
        "queries": ["typed route"],
        "sink_backend": "cortex",
    }


def _thread_payload() -> dict[str, Any]:
    return {
        "thread": "15226",
        "subject": "batch5 sidecar parity",
        "content": "# Findings\n\nTyped vs dispatch parity.",
        "from_agent": "cursor-sdk",
        "execution_id": "exec-batch5-1",
        "oversized": False,
        "sidecar_slug": None,
    }


def _todo_payload() -> dict[str, Any]:
    return {
        "todo_id": "todo:batch5-close-parity",
        "summary": "Closure sidecar stamped in batch 5.",
        "evidence": "pytest twin roots",
        "reasoning_summary": "parity across paths",
        "references": [{"target": "todo:cortex-dispatch-typed-route-migration", "role": "parent"}],
        "agent": "cursor-sdk",
        "session_id": "sess-batch5",
        "closed_at": "2026-10-05T12:00:00+00:00",
    }


def _pinned_payload() -> dict[str, Any]:
    return {
        "rel_path": "notes/system/dispatch/batch5-pinned.md",
        "content": "# Pinned deliverable\n\nbatch 5 content.",
        "write_if_absent": False,
        "dispatch_id": "dispatch-15226",
        "thread_id": "15226",
    }


@pytest.mark.offline
def test_recon_sidecar_write_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _recon_payload()
    dispatch_files = tmp_path / "files_dispatch_recon"
    typed_files = tmp_path / "files_typed_recon"
    dispatch_files.mkdir()
    typed_files.mkdir()
    _assert_not_live_files_root(dispatch_files)

    bind_db = tmp_path / "cortex_dispatch_recon.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("recon_sidecar_write", payload)
    assert "error" not in dispatch_raw
    dispatch_body = _normalize_response(dispatch_raw)
    dispatch_tree = _file_tree(dispatch_files)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_recon",
        files_root=typed_files,
    )
    resp = typed_client.post("/recon/sidecars", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(typed_files) == dispatch_tree
    assert dispatch_tree


@pytest.mark.offline
def test_thread_sidecar_write_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _thread_payload()
    dispatch_files = tmp_path / "files_dispatch_thread"
    typed_files = tmp_path / "files_typed_thread"
    dispatch_files.mkdir()
    typed_files.mkdir()

    bind_db = tmp_path / "cortex_dispatch_thread.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("thread_sidecar_write", payload)
    assert "error" not in dispatch_raw
    dispatch_body = _normalize_response(dispatch_raw)
    dispatch_tree = _file_tree(dispatch_files)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_thread",
        files_root=typed_files,
    )
    resp = typed_client.post("/threads/sidecars", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(typed_files) == dispatch_tree
    assert dispatch_tree


@pytest.mark.offline
def test_todo_close_sidecar_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _todo_payload()
    todo_id = payload["todo_id"]
    dispatch_files = tmp_path / "files_dispatch_todo"
    typed_files = tmp_path / "files_typed_todo"
    dispatch_files.mkdir()
    typed_files.mkdir()

    def seed(conn: sqlite3.Connection) -> None:
        insert_entity(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            name="Batch 5 todo",
        )
        conn.commit()

    bind_db = tmp_path / "cortex_dispatch_todo.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    seed(cortex_db.cortex_conn())
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("todo_close_sidecar", payload)
    assert dispatch_raw.get("ok") is True
    dispatch_body = _normalize_response(dispatch_raw)
    dispatch_tree = _file_tree(dispatch_files)
    dispatch_attrs = _entity_attributes(cortex_db.cortex_conn(), todo_id)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_todo",
        files_root=typed_files,
    )
    seed(cortex_db.cortex_conn())
    resp = typed_client.post("/todos/closure-sidecars", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(typed_files) == dispatch_tree
    assert _entity_attributes(cortex_db.cortex_conn(), todo_id) == dispatch_attrs


@pytest.mark.offline
def test_pinned_deliverable_write_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _pinned_payload()
    dispatch_files = tmp_path / "files_dispatch_pinned"
    typed_files = tmp_path / "files_typed_pinned"
    dispatch_files.mkdir()
    typed_files.mkdir()

    bind_db = tmp_path / "cortex_dispatch_pinned.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("pinned_deliverable_write", payload)
    assert "error" not in dispatch_raw
    dispatch_body = _normalize_response(dispatch_raw)
    dispatch_tree = _file_tree(dispatch_files)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_pinned",
        files_root=typed_files,
    )
    resp = typed_client.post("/pinned-deliverables", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(typed_files) == dispatch_tree
    assert "notes/system/dispatch/batch5-pinned.md" in dispatch_tree


@pytest.mark.offline
def test_todo_close_sidecar_failure_parity_after_file_write(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial failure: closure file exists while entity attribute update fails."""
    payload = _todo_payload()
    todo_id = payload["todo_id"]
    dispatch_files = tmp_path / "files_dispatch_todo_fail"
    typed_files = tmp_path / "files_typed_todo_fail"
    dispatch_files.mkdir()
    typed_files.mkdir()

    def seed(conn: sqlite3.Connection) -> None:
        insert_entity(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            name="Batch 5 todo fail",
        )
        conn.commit()

    def failing_update(**kwargs: object) -> dict[str, Any]:
        return {"error": "injected entity update failure"}

    bind_db = tmp_path / "cortex_dispatch_todo_fail.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    seed(cortex_db.cortex_conn())
    _bind_isolated_files(monkeypatch, dispatch_files)
    monkeypatch.setattr(ops_todos_mod, "_op_entity_update", failing_update)
    dispatch_raw = execute_op("todo_close_sidecar", payload)
    assert dispatch_raw.get("ok") is False
    assert "attribute_update_error" in dispatch_raw
    dispatch_body = _normalize_response(dispatch_raw)
    dispatch_tree = _file_tree(dispatch_files)
    dispatch_attrs = _entity_attributes(cortex_db.cortex_conn(), todo_id)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_todo_fail",
        files_root=typed_files,
    )
    seed(cortex_db.cortex_conn())
    monkeypatch.setattr(ops_todos_mod, "_op_entity_update", failing_update)
    resp = typed_client.post("/todos/closure-sidecars", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(typed_files) == dispatch_tree
    expected_closure = "notes/system/todos/batch5-close-parity-closure.md"
    assert set(dispatch_tree) == {expected_closure}
    assert "closure_summary_uri" not in dispatch_attrs
    typed_attrs = _entity_attributes(cortex_db.cortex_conn(), todo_id)
    assert set(_file_tree(typed_files)) == {expected_closure}
    assert "closure_summary_uri" not in typed_attrs
    assert typed_attrs == dispatch_attrs


@pytest.mark.offline
def test_thread_sidecar_oversized_null_s4_divergence_dispatch_vs_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4 known divergence: dispatch accepts oversized=null (renders 'none'); typed 422."""
    payload = {
        "thread": "999",
        "subject": "null-oversized",
        "content": "body text\n",
        "oversized": None,
    }
    dispatch_files = tmp_path / "files_dispatch_oversized_null"
    typed_files = tmp_path / "files_typed_oversized_null"
    dispatch_files.mkdir()
    typed_files.mkdir()

    bind_db = tmp_path / "cortex_dispatch_oversized_null.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("thread_sidecar_write", payload)
    assert "error" not in dispatch_raw
    dispatch_tree = _file_tree(dispatch_files)
    assert dispatch_tree
    sidecar_bytes = next(iter(dispatch_tree.values()))
    assert b"oversized: none" in sidecar_bytes

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_oversized_null",
        files_root=typed_files,
    )
    resp = typed_client.post("/threads/sidecars", json=payload)
    assert resp.status_code == 422, resp.text
    assert _file_tree(typed_files) == {}


@pytest.mark.offline
def test_thread_sidecar_write_unexpanded_shell_typed_and_dispatch_match(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4 known gap: both paths reject shell content before any file write."""
    payload = {
        "thread": "1",
        "subject": "shell",
        "content": "$(cat missing-sidecar.md)",
    }
    dispatch_files = tmp_path / "files_dispatch_shell"
    typed_files = tmp_path / "files_typed_shell"
    dispatch_files.mkdir()
    typed_files.mkdir()

    bind_db = tmp_path / "cortex_dispatch_shell.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    _bind_isolated_files(monkeypatch, dispatch_files)
    dispatch_raw = execute_op("thread_sidecar_write", payload)
    assert dispatch_raw.get("error") == "sidecar_unexpanded_shell"
    dispatch_body = _normalize_response(dispatch_raw)

    typed_client = _isolated_client(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        suffix="typed_shell",
        files_root=typed_files,
    )
    resp = typed_client.post("/threads/sidecars", json=payload)
    assert resp.status_code == 200, resp.text
    typed_body = _normalize_response(resp.json())
    assert dispatch_body == typed_body
    assert _file_tree(dispatch_files) == _file_tree(typed_files) == {}


@pytest.mark.offline
def test_batch5_router_resolution_forward_match_baseline_routes(
    migrated_db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    app = create_app(db_path=str(migrated_db_path))
    assert_baseline_route_resolution(app)
    assert (
        resolve_endpoint_name(app, "POST", "/recon/sidecars")
        == "recon_sidecar_write_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/threads/sidecars")
        == "thread_sidecar_write_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/todos/closure-sidecars")
        == "todo_close_sidecar_route"
    )
    assert (
        resolve_endpoint_name(app, "POST", "/pinned-deliverables")
        == "pinned_deliverable_write_route"
    )
