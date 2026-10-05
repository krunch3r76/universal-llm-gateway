"""S6 batch 7: L1–L3 WRITE_LOCK semantics for stamped ops."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store._write_lock_semantics_testkit import (
    assert_l1_acquisition_parity,
    assert_l3_lock_reacquirable,
    install_counting_write_lock,
    run_l2_serialization,
    trace_txn_boundaries,
)
from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_composites as composites_mod
from cortex_store.dispatch_ops import ops_views as views_mod
from cortex_store.main import create_app


def _skill_workspace(tmp_path: Path, skill_id: str) -> tuple[str, str]:
    ws_root = tmp_path / "projects"
    skill_dir = ws_root / "universal-llm-gateway" / ".cursor" / "skills" / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# batch7\n", encoding="utf-8")
    canonical = (
        f"workspaces://universal-llm-gateway/.cursor/skills/{skill_id}/SKILL.md"
    )
    return str(ws_root), canonical


def _insert_case(conn: Any, case_id: str) -> None:
    insert_entity(conn, entity_id=case_id, entity_type="case", name=case_id)
    conn.commit()


def _seed_relationship_types(conn: Any) -> None:
    for rel_type in ("keystone_of", "uses_skill"):
        conn.execute(
            "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
            (rel_type, f"batch7 {rel_type}"),
        )
    conn.commit()


def _register_payload(skill_id: str, canonical: str) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "skill_path": canonical,
        "case_id": "case:batch7-lock",
        "description": "batch7 lock semantics",
        "trigger_phrases": ["batch7", "lock"],
        "skill_binding": {"skill_class": "protocol"},
        "session_id": "cursor-2026-10-05-lock-batch7",
        "agent": "cursor-sdk",
    }


def _isolated_app(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    suffix: str,
    skill_id: str,
) -> tuple[TestClient, dict[str, Any]]:
    db_path = tmp_path / f"cortex_{suffix}.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    ws_root, canonical = _skill_workspace(tmp_path / f"ws_{suffix}", skill_id)
    monkeypatch.setenv("WORKSPACES_ROOT", ws_root)
    client = TestClient(
        create_app(db_path=str(db_path)),
        raise_server_exceptions=False,
    )
    return client, _register_payload(skill_id, canonical)


@pytest.mark.offline
def test_register_skill_substrate_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_id = "batch7-l1-dispatch"
    db_path = tmp_path / "cortex_l1_dispatch.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    ws_root, canonical = _skill_workspace(tmp_path / "ws_l1_d", skill_id)
    monkeypatch.setenv("WORKSPACES_ROOT", ws_root)

    conn = cortex_db.cortex_conn()
    _seed_relationship_types(conn)
    _insert_case(conn, "case:batch7-lock")
    counter = install_counting_write_lock(monkeypatch, composites_mod)
    trace = trace_txn_boundaries(monkeypatch, composites_mod, counter)
    result = execute_op(
        "register_skill_substrate", _register_payload(skill_id, canonical)
    )
    assert "error" not in result, result
    assert_l1_acquisition_parity(counter, trace, expect_txn=True)


@pytest.mark.offline
def test_register_skill_substrate_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_id = "batch7-l1-typed"
    client, payload = _isolated_app(
        migrated_db_template, tmp_path, monkeypatch, suffix="l1_typed", skill_id=skill_id
    )
    conn = cortex_db.cortex_conn()
    _seed_relationship_types(conn)
    _insert_case(conn, "case:batch7-lock")
    counter = install_counting_write_lock(monkeypatch, composites_mod)
    trace = trace_txn_boundaries(monkeypatch, composites_mod, counter)
    resp = client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 200, resp.text
    assert "error" not in resp.json(), resp.json()
    assert_l1_acquisition_parity(counter, trace, expect_txn=True)


@pytest.mark.offline
def test_register_skill_substrate_l2_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_id = "batch7-l2"
    client, payload = _isolated_app(
        migrated_db_template, tmp_path, monkeypatch, suffix="l2", skill_id=skill_id
    )
    conn = cortex_db.cortex_conn()
    _seed_relationship_types(conn)
    _insert_case(conn, "case:batch7-lock")
    counter = install_counting_write_lock(monkeypatch, composites_mod)

    run_l2_serialization(
        counter,
        lambda: execute_op("register_skill_substrate", payload),
    )
    counter2 = install_counting_write_lock(monkeypatch, composites_mod)
    run_l2_serialization(
        counter2,
        lambda: client.post("/skills/register-substrate", json=payload).json(),
    )


@pytest.mark.offline
def test_register_skill_substrate_l3_failure_release(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inject after first entity insert inside the atomic create path."""
    from cortex_store.entity_crud import create_entity_impl

    skill_id = "batch7-l3"
    client, payload = _isolated_app(
        migrated_db_template, tmp_path, monkeypatch, suffix="l3", skill_id=skill_id
    )
    conn = cortex_db.cortex_conn()
    _seed_relationship_types(conn)
    _insert_case(conn, "case:batch7-lock")
    original = create_entity_impl
    state = {"seen_skill": False}

    def failing_impl(conn: sqlite3.Connection, body: dict[str, Any], **kw: Any) -> Any:
        if body.get("type") == "agent_skill":
            state["seen_skill"] = True
            return original(conn, body, **kw)
        if state["seen_skill"]:
            raise sqlite3.OperationalError("injected after skill entity insert")
        return original(conn, body, **kw)

    monkeypatch.setattr(composites_mod, "create_entity_impl", failing_impl)

    counter = install_counting_write_lock(monkeypatch, composites_mod)
    with pytest.raises(sqlite3.OperationalError, match="injected after skill entity"):
        execute_op("register_skill_substrate", payload)
    assert_l3_lock_reacquirable(counter)

    counter2 = install_counting_write_lock(monkeypatch, composites_mod)
    resp = client.post("/skills/register-substrate", json=payload)
    assert resp.status_code == 500
    assert_l3_lock_reacquirable(counter2)


@pytest.mark.offline
def test_view_render_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _shared as shared_mod
    from cortex_store.dispatch_ops.ops_entities import _op_entity_create

    files_root = tmp_path / "files_l1_d"
    files_root.mkdir()
    db_path = tmp_path / "cortex_l1_vr_d.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)

    root = _op_entity_create(id="case:batch7-vr", type="case", name="vr")
    doc = _op_entity_create(
        id="document:batch7-vr-charter",
        type="document",
        name="charter",
        source_uri="cortex://notes/views/batch7-charter.md",
    )
    assert "error" not in root and "error" not in doc

    reg = execute_op(
        "view_render",
        {
            "document_id": "document:batch7-vr-charter",
            "mode": "register",
            "root_id": "case:batch7-vr",
            "view_profile": "matter_charter",
        },
    )
    assert "error" not in reg, reg
    as_of = (reg.get("stamp") or {}).get("time") or "2026-07-12T12:00:00+00:00"

    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, cortex_db, counter)
    read = execute_op(
        "view_render",
        {
            "document_id": "document:batch7-vr-charter",
            "mode": "read_asof",
            "as_of_system": as_of,
        },
    )
    assert "error" not in read or read.get("code") == "as_of_instance_not_found"
    assert counter.acquires == 1
    assert counter.releases == 1
    assert not counter.locked()


@pytest.mark.offline
def test_view_render_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _shared as shared_mod
    from cortex_store.dispatch_ops.ops_entities import _op_entity_create

    files_root = tmp_path / "files_l1_t"
    files_root.mkdir()
    db_path = tmp_path / "cortex_l1_vr_t.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)

    _op_entity_create(id="case:batch7-vr-t", type="case", name="vr")
    _op_entity_create(
        id="document:batch7-vr-t-charter",
        type="document",
        name="charter",
        source_uri="cortex://notes/views/batch7-t-charter.md",
    )
    reg = execute_op(
        "view_render",
        {
            "document_id": "document:batch7-vr-t-charter",
            "mode": "register",
            "root_id": "case:batch7-vr-t",
            "view_profile": "matter_charter",
        },
    )
    as_of = (reg.get("stamp") or {}).get("time") or "2026-07-12T12:00:00+00:00"
    client = TestClient(create_app(db_path=str(db_path)))

    counter = install_counting_write_lock(monkeypatch, views_mod)
    resp = client.get(
        "/views/document:batch7-vr-t-charter",
        params={"asof": as_of, "mode": "read_asof"},
    )
    assert resp.status_code == 200, resp.text
    assert counter.acquires == 1
    assert counter.releases == 1


@pytest.mark.offline
def test_view_render_l2_typed_read_asof(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _shared as shared_mod
    from cortex_store.dispatch_ops.ops_entities import _op_entity_create

    files_root = tmp_path / "files_l2"
    files_root.mkdir()
    db_path = tmp_path / "cortex_l2.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
    _op_entity_create(id="case:batch7-l2", type="case", name="vr")
    _op_entity_create(
        id="document:batch7-l2-charter",
        type="document",
        name="c",
        source_uri="cortex://notes/views/batch7-l2.md",
    )
    reg = execute_op(
        "view_render",
        {
            "document_id": "document:batch7-l2-charter",
            "mode": "register",
            "root_id": "case:batch7-l2",
            "view_profile": "matter_charter",
        },
    )
    as_of = (reg.get("stamp") or {}).get("time") or "2026-07-12T12:00:00+00:00"
    client = TestClient(create_app(db_path=str(db_path)))
    counter = install_counting_write_lock(monkeypatch, views_mod)

    run_l2_serialization(
        counter,
        lambda: client.get(
            "/views/document:batch7-l2-charter",
            params={"asof": as_of, "mode": "read_asof"},
        ).json(),
    )


@pytest.mark.offline
def test_view_render_l3_failure_release(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store.dispatch_ops import _shared as shared_mod
    from cortex_store.dispatch_ops._views import archive as archive_mod
    from cortex_store.dispatch_ops.ops_entities import _op_entity_create

    files_root = tmp_path / "files_l3"
    files_root.mkdir()
    db_path = tmp_path / "cortex_l3.db"
    copy_template_db(migrated_db_template, db_path)
    bind_cortex_db(monkeypatch, db_path)
    monkeypatch.setattr(shared_mod, "_FILES_ROOT", files_root)
    monkeypatch.setattr(views_mod, "_FILES_ROOT", files_root)
    _op_entity_create(id="case:batch7-l3", type="case", name="vr")
    _op_entity_create(
        id="document:batch7-l3-charter",
        type="document",
        name="c",
        source_uri="cortex://notes/views/batch7-l3.md",
    )
    reg = execute_op(
        "view_render",
        {
            "document_id": "document:batch7-l3-charter",
            "mode": "register",
            "root_id": "case:batch7-l3",
            "view_profile": "matter_charter",
        },
    )
    as_of = (reg.get("stamp") or {}).get("time") or "2026-07-12T12:00:00+00:00"

    original_read = archive_mod.read_asof_instance

    def fail_read(*args: Any, **kwargs: Any) -> Any:
        raise OSError("injected read_asof failure")

    monkeypatch.setattr(views_mod, "read_asof_instance", fail_read)
    monkeypatch.setattr(archive_mod, "read_asof_instance", fail_read)

    counter = install_counting_write_lock(monkeypatch, views_mod)
    with pytest.raises(OSError, match="injected read_asof"):
        execute_op(
            "view_render",
            {
                "document_id": "document:batch7-l3-charter",
                "mode": "read_asof",
                "as_of_system": as_of,
            },
        )
    assert_l3_lock_reacquirable(counter)
