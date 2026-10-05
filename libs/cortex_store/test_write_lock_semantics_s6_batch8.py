"""S6 batch 8: L1–L3 WRITE_LOCK semantics for entity_retype and endeavor_repair_t1."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store import entity_rekey_core as rekey_core
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store._write_lock_semantics_testkit import (
    assert_l1_acquisition_parity,
    assert_l3_lock_reacquirable,
    install_counting_write_lock,
    run_l2_serialization,
    trace_txn_boundaries,
)
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops import ops_endeavor_birth as endeavor_mod
from cortex_store.dispatch_ops import ops_entities as entities_mod
from cortex_store.endeavor_birth.repair import _T1_HOST
from cortex_store.main import create_app
from cortex_store.test_stamped_substrate_s6_batch8_parity import (
    _assert_no_reference_to_id,
    _bind_isolated_db,
    _full_retype_inventory,
    _seed_retype_surfaces,
)


def _seed_t1_pre_repair(conn: sqlite3.Connection) -> None:
    insert_entity(
        conn,
        entity_id=_T1_HOST,
        entity_type="opportunity",
        name=_T1_HOST,
        attributes=json.dumps({"bus_thread": "5129", "mode": "endeavor"}),
    )
    conn.commit()


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


@pytest.mark.offline
def test_entity_retype_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_l1_dispatch.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    _seed_retype_surfaces(cortex_db.cortex_conn(), "agent_skill:batch8-lock")
    counter = install_counting_write_lock(monkeypatch, entities_mod)
    trace = trace_txn_boundaries(monkeypatch, entities_mod, counter)
    result = execute_op(
        "entity_retype",
        {"entity_id": "agent_skill:batch8-lock", "new_type": "rule"},
    )
    assert "error" not in result, result
    assert_l1_acquisition_parity(counter, trace, expect_txn=True)


@pytest.mark.offline
def test_entity_retype_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l1_typed"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), "agent_skill:batch8-lock")
    counter = install_counting_write_lock(monkeypatch, entities_mod)
    trace = trace_txn_boundaries(monkeypatch, entities_mod, counter)
    resp = client.post(
        "/entities/agent_skill:batch8-lock/retype",
        json={"new_type": "rule"},
    )
    assert resp.status_code == 200, resp.text
    assert "error" not in resp.json(), resp.json()
    assert_l1_acquisition_parity(counter, trace, expect_txn=True)


@pytest.mark.offline
def test_entity_retype_l2_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l2"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), "agent_skill:batch8-lock")
    payload = {"entity_id": "agent_skill:batch8-lock", "new_type": "rule"}
    counter = install_counting_write_lock(monkeypatch, entities_mod)
    run_l2_serialization(
        counter,
        lambda: execute_op("entity_retype", payload),
    )
    counter2 = install_counting_write_lock(monkeypatch, entities_mod)
    run_l2_serialization(
        counter2,
        lambda: client.post(
            "/entities/agent_skill:batch8-lock/retype",
            json={"new_type": "rule"},
        ).json(),
    )


@pytest.mark.offline
def test_entity_retype_l3_failure_release(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "agent_skill:batch8-l3"
    new_id = "rule:batch8-l3"
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="l3"
    )
    _seed_retype_surfaces(cortex_db.cortex_conn(), entity_id)
    pre = _full_retype_inventory(cortex_db.cortex_conn(), entity_id)
    original = rekey_core.rewrite_simple_column

    def fail_after_first(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        old_id: str,
        new_id: str,
    ) -> None:
        original(conn, table, column, old_id, new_id)
        raise sqlite3.OperationalError("injected after first rewrite")

    monkeypatch.setattr(rekey_core, "rewrite_simple_column", fail_after_first)

    counter = install_counting_write_lock(monkeypatch, entities_mod)
    with pytest.raises(sqlite3.OperationalError, match="injected after first rewrite"):
        execute_op("entity_retype", {"entity_id": entity_id, "new_type": "rule"})
    assert_l3_lock_reacquirable(counter)
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre
    _assert_no_reference_to_id(cortex_db.cortex_conn(), new_id)

    counter2 = install_counting_write_lock(monkeypatch, entities_mod)
    resp = client.post(f"/entities/{entity_id}/retype", json={"new_type": "rule"})
    assert resp.status_code == 500
    assert_l3_lock_reacquirable(counter2)
    assert _full_retype_inventory(cortex_db.cortex_conn(), entity_id) == pre
    _assert_no_reference_to_id(cortex_db.cortex_conn(), new_id)


@pytest.mark.offline
def test_endeavor_repair_t1_l1_dispatch_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "cortex_repair_l1_d.db"
    copy_template_db(migrated_db_template, db_path)
    _bind_isolated_db(monkeypatch, db_path)
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, endeavor_mod)
    trace = trace_txn_boundaries(monkeypatch, endeavor_mod, counter)
    result = execute_op("endeavor_repair_t1", {})
    assert result.get("applied") is True, result
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_endeavor_repair_t1_l1_typed_path(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="repair_l1_t"
    )
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, endeavor_mod)
    trace = trace_txn_boundaries(monkeypatch, endeavor_mod, counter)
    resp = client.post("/endeavors/repair-t1", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("applied") is True
    assert_l1_acquisition_parity(counter, trace, expect_txn=False)


@pytest.mark.offline
def test_endeavor_repair_t1_l2_dispatch_and_typed(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="repair_l2"
    )
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    counter = install_counting_write_lock(monkeypatch, endeavor_mod)
    run_l2_serialization(counter, lambda: execute_op("endeavor_repair_t1", {}))
    counter2 = install_counting_write_lock(monkeypatch, endeavor_mod)
    run_l2_serialization(
        counter2,
        lambda: client.post("/endeavors/repair-t1", json={}).json(),
    )


@pytest.mark.offline
def test_endeavor_repair_t1_l3_failure_release(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single persisted effect: ``repair.py:54-57`` UPDATE entities — inject after it."""
    from cortex_store.endeavor_birth import repair as repair_mod

    client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="repair_l3"
    )
    _seed_t1_pre_repair(cortex_db.cortex_conn())
    original_apply = repair_mod.apply_5129_repair

    class _InjectConn:
        __slots__ = ("_inner", "_injected")

        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner
            self._injected = False

        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
            cur = self._inner.execute(sql, params)
            if (
                isinstance(sql, str)
                and "UPDATE entities SET attributes" in sql
                and not self._injected
            ):
                self._injected = True
                raise sqlite3.OperationalError("injected after T1 attributes UPDATE")
            return cur

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def failing_apply(conn: sqlite3.Connection) -> dict[str, Any]:
        return original_apply(_InjectConn(conn))

    monkeypatch.setattr(repair_mod, "apply_5129_repair", failing_apply)
    counter = install_counting_write_lock(monkeypatch, endeavor_mod)
    with pytest.raises(sqlite3.OperationalError, match="injected after T1"):
        execute_op("endeavor_repair_t1", {})
    assert_l3_lock_reacquirable(counter)
    attrs_before = json.loads(
        cortex_db.cortex_conn()
        .execute("SELECT attributes FROM entities WHERE id = ?", (_T1_HOST,))
        .fetchone()[0]
    )
    assert attrs_before.get("bus_thread") == "5129"
    assert "endeavor_charter_uri" not in attrs_before

    counter2 = install_counting_write_lock(monkeypatch, endeavor_mod)
    resp = client.post("/endeavors/repair-t1", json={})
    assert resp.status_code == 500
    assert_l3_lock_reacquirable(counter2)
    attrs_typed = json.loads(
        cortex_db.cortex_conn()
        .execute("SELECT attributes FROM entities WHERE id = ?", (_T1_HOST,))
        .fetchone()[0]
    )
    assert attrs_typed.get("bus_thread") == "5129"
