"""S6 batch 9: L1–L3 WRITE_LOCK semantics for view_render."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store import entity_crud
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store._write_lock_semantics_testkit import (
    assert_l1_acquisition_parity,
    assert_l3_lock_reacquirable,
    install_counting_write_lock,
    run_l2_serialization,
    trace_txn_boundaries,
)
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_views as views_mod
from cortex_store.main import create_app
from cortex_store.test_stamped_substrate_s6_batch9_parity import (
    _DOC_ID,
    _NARRATIVE,
    _ROOT_ID,
    _bind_files_root,
    _bind_isolated_db,
    _seed_pending_refresh_delta,
    _seed_view_entities,
)

_REGISTER_PAYLOAD = {
    "document_id": _DOC_ID,
    "mode": "register",
    "root_id": _ROOT_ID,
    "view_profile": "matter_charter",
    "narrative_sections": _NARRATIVE,
}


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
    _bind_files_root(monkeypatch, tmp_path / f"files_{suffix}")
    return TestClient(create_app(db_path=str(db_path)), raise_server_exceptions=False)


@pytest.mark.offline
def test_view_render_refresh_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l1_refresh_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    files_root = _bind_files_root(monkeypatch, tmp_path / "files_l1_d")
    _seed_pending_refresh_delta(cortex_db.cortex_conn(), files_root)
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    result = execute_op(
        "view_render",
        {"document_id": _DOC_ID, "mode": "refresh", "root_id": _ROOT_ID},
    )
    assert "error" not in result, result
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_view_render_refresh_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l1_refresh_t"
    )
    _seed_pending_refresh_delta(
        cortex_db.cortex_conn(), tmp_path / "files_l1_refresh_t"
    )
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    resp = client.post(
        f"/views/{_DOC_ID}/render",
        json={"mode": "refresh", "root_id": _ROOT_ID},
    )
    assert resp.status_code == 200, resp.text
    assert "error" not in resp.json(), resp.json()
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_view_render_register_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l1_reg_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    _bind_files_root(monkeypatch, tmp_path / "files_l1_reg_d")
    _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    result = execute_op("view_render", _REGISTER_PAYLOAD)
    assert "error" not in result, result
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_view_render_register_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l1_reg_t"
    )
    _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
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
    assert "error" not in resp.json(), resp.json()
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_view_render_refresh_l2_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l2_refresh_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    files_root = _bind_files_root(monkeypatch, tmp_path / "files_l2_refresh_d")
    _seed_pending_refresh_delta(cortex_db.cortex_conn(), files_root)
    counter = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(
        counter,
        lambda: execute_op(
            "view_render",
            {"document_id": _DOC_ID, "mode": "refresh", "root_id": _ROOT_ID},
        ),
    )


@pytest.mark.offline
def test_view_render_refresh_l2_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l2_refresh_t"
    )
    _seed_pending_refresh_delta(
        cortex_db.cortex_conn(), tmp_path / "files_l2_refresh_t"
    )
    counter = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(
        counter,
        lambda: client.post(
            f"/views/{_DOC_ID}/render",
            json={"mode": "refresh", "root_id": _ROOT_ID},
        ).json(),
    )


@pytest.mark.offline
def test_view_render_register_l2_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l2_reg_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    _bind_files_root(monkeypatch, tmp_path / "files_l2_reg_d")
    _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(counter, lambda: execute_op("view_render", _REGISTER_PAYLOAD))


@pytest.mark.offline
def test_view_render_register_l2_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l2_reg_t"
    )
    _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(
        counter,
        lambda: client.post(
            f"/views/{_DOC_ID}/render",
            json={
                "mode": "register",
                "root_id": _ROOT_ID,
                "view_profile": "matter_charter",
                "narrative_sections": _NARRATIVE,
            },
        ).json(),
    )


@pytest.mark.offline
def test_view_render_refresh_l3_failure_release(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l3_refresh_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    files_root = _bind_files_root(monkeypatch, tmp_path / "files_l3_refresh_d")
    _seed_pending_refresh_delta(cortex_db.cortex_conn(), files_root)

    original_update = entity_crud.update_entity_impl

    def fail_refresh_update(*args, **kwargs):
        entity_id = kwargs.get("entity_id") or (args[1] if len(args) >= 2 else None)
        updates = kwargs.get("updates") or (args[2] if len(args) >= 3 else None)
        if entity_id == _DOC_ID and isinstance(updates, dict) and "attributes" in updates:
            raise RuntimeError("injected refresh update_entity_impl")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(entity_crud, "update_entity_impl", fail_refresh_update)
    monkeypatch.setattr(views_mod, "update_entity_impl", fail_refresh_update)
    counter = install_counting_write_lock(monkeypatch, views_mod)
    with pytest.raises(RuntimeError, match="injected refresh update_entity_impl"):
        execute_op(
            "view_render",
            {"document_id": _DOC_ID, "mode": "refresh", "root_id": _ROOT_ID},
        )
    assert_l3_lock_reacquirable(counter)
    monkeypatch.setattr(entity_crud, "update_entity_impl", original_update)
    monkeypatch.setattr(views_mod, "update_entity_impl", original_update)

    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l3_refresh_t"
    )
    _seed_pending_refresh_delta(
        cortex_db.cortex_conn(), tmp_path / "files_l3_refresh_t"
    )
    monkeypatch.setattr(entity_crud, "update_entity_impl", fail_refresh_update)
    monkeypatch.setattr(views_mod, "update_entity_impl", fail_refresh_update)
    counter2 = install_counting_write_lock(monkeypatch, views_mod)
    resp = client.post(
        f"/views/{_DOC_ID}/render",
        json={"mode": "refresh", "root_id": _ROOT_ID},
    )
    assert resp.status_code == 500
    assert_l3_lock_reacquirable(counter2)
