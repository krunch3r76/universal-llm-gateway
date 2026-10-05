"""S6 batch 9: L1–L3 WRITE_LOCK semantics for view_render."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
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
    _bind_files_root,
    _bind_isolated_db,
    _seed_registered_view,
    _seed_view_entities,
)


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
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    _seed_registered_view(cortex_db.cortex_conn(), files_root, root_id, doc_id)
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    result = execute_op("view_render", {"document_id": doc_id, "mode": "refresh"})
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
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    _seed_registered_view(
        cortex_db.cortex_conn(),
        tmp_path / "files_l1_refresh_t",
        root_id,
        doc_id,
    )
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    resp = client.post(f"/views/{doc_id}/render", json={"mode": "refresh"})
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
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    result = execute_op(
        "view_render",
        {
            "document_id": doc_id,
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": {
                "narrative_layer": "Grounding cites [assertion:1]."
            },
        },
    )
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
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, views_mod)
    trace = trace_txn_boundaries(monkeypatch, views_mod, counter)
    resp = client.post(
        f"/views/{doc_id}/render",
        json={
            "mode": "register",
            "root_id": root_id,
            "view_profile": "matter_charter",
            "narrative_sections": {
                "narrative_layer": "Grounding cites [assertion:1]."
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert "error" not in resp.json(), resp.json()
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_view_render_l2_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l2"
    )
    root_id, doc_id = _seed_view_entities(cortex_db.cortex_conn())
    _seed_registered_view(
        cortex_db.cortex_conn(), tmp_path / "files_l2", root_id, doc_id
    )
    payload = {"document_id": doc_id, "mode": "refresh"}
    counter = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(
        counter,
        lambda: execute_op("view_render", payload),
    )
    counter2 = install_counting_write_lock(monkeypatch, views_mod)
    run_l2_serialization(
        counter2,
        lambda: client.post(f"/views/{doc_id}/render", json={"mode": "refresh"}).json(),
    )
