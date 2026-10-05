"""S6 batch 2: dispatch vs typed parity for observe + friction family."""

from __future__ import annotations

import copy
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store import db as cortex_db
from cortex_store._intent_card_test_fixtures import insert_entity
from cortex_store._test_db_bootstrap import copy_template_db
from cortex_store.conftest import bind_cortex_db
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops.test_friction_to_todo import (
    _insert_friction,
    _patch_supersede_side_effects,
    _seed_skill_entity,
)
from cortex_store.main import create_app

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})
_VOLATILE_ITEM_KEYS = frozenset(
    {"id", "observed_at", "created_at", "updated_at", "claim_hash", "quality_score"}
)


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _VOLATILE_ITEM_KEYS}


def _normalize_write_result(body: dict[str, Any]) -> dict[str, Any]:
    body = _without_dispatch_envelope(body)
    if "item" in body and isinstance(body["item"], dict):
        normalized = copy.deepcopy(body)
        normalized["item"] = _normalize_item(body["item"])
        return normalized
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
    bind_cortex_db(monkeypatch, db_path)
    return TestClient(create_app(db_path=str(db_path)))


def _assert_read_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    op: str,
    dispatch_args: dict[str, Any],
    path: str,
    params: dict[str, Any] | None = None,
    seed: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    bind_db = tmp_path / "cortex_dispatch_read.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    if seed is not None:
        seed(cortex_db.cortex_conn())
    dispatch_body = _without_dispatch_envelope(execute_op(op, dispatch_args))

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_read"
    )
    if seed is not None:
        seed(cortex_db.cortex_conn())
    http_resp = typed_client.get(path, params=params or {})
    assert http_resp.status_code == 200, http_resp.text
    assert dispatch_body == _without_dispatch_envelope(http_resp.json())


def _assert_write_parity(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    op: str,
    dispatch_args: dict[str, Any],
    method_path: str,
    json_body: dict[str, Any],
    seed: Callable[[sqlite3.Connection], None] | None = None,
) -> None:
    bind_db = tmp_path / "cortex_dispatch_write.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    if seed is not None:
        seed(cortex_db.cortex_conn())
    dispatch_body = _normalize_write_result(execute_op(op, dispatch_args))

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write"
    )
    if seed is not None:
        seed(cortex_db.cortex_conn())
    http_resp = typed_client.post(method_path, json=json_body)
    assert http_resp.status_code == 200, http_resp.text
    typed_body = _normalize_write_result(http_resp.json())
    assert dispatch_body == typed_body


@pytest.mark.offline
def test_observe_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entity_id = "decision:s6-observe-parity"

    def seed(conn: sqlite3.Connection) -> None:
        insert_entity(conn, entity_id=entity_id, entity_type="decision")

    args = {
        "entity_id": entity_id,
        "claim": "S6 batch 2 observe parity fixture.",
        "confidence": "believed",
        "agent": "pytest",
    }
    _assert_write_parity(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        op="observe",
        dispatch_args=args,
        method_path="/assertions/observations",
        json_body=args,
        seed=seed,
    )


@pytest.mark.offline
def test_friction_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_supersede_side_effects(monkeypatch)
    service_id = "service:s6-friction-parity"

    def seed(conn: sqlite3.Connection) -> None:
        insert_entity(
            conn,
            entity_id=service_id,
            entity_type="service",
            name="parity service",
        )

    args = {
        "owner": service_id,
        "category": "tool_error",
        "note": "S6 batch 2 friction parity.",
        "agent": "pytest",
        "actionable": False,
        "defer_enqueue": True,
    }
    _assert_write_parity(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        op="friction",
        dispatch_args=args,
        method_path="/frictions",
        json_body=args,
        seed=seed,
    )


@pytest.mark.offline
def test_frictions_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_supersede_side_effects(monkeypatch)

    def seed(conn: sqlite3.Connection) -> None:
        _seed_skill_entity(conn)
        _insert_friction(conn, "[tool_error] list parity seed")

    params = {"category": "tool_error", "intent": "summary", "limit": 5}
    _assert_read_parity(
        migrated_db_template,
        tmp_path,
        monkeypatch,
        op="frictions",
        dispatch_args=params,
        path="/frictions",
        params=params,
        seed=seed,
    )


@pytest.mark.offline
def test_friction_close_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_supersede_side_effects(monkeypatch)
    body = {
        "resolution_kind": "wontfix",
        "agent": "pytest",
        "session_id": "s6-batch2-parity",
        "resolution_note": "parity close",
    }

    def seed_and_close_dispatch(conn: sqlite3.Connection) -> int:
        _seed_skill_entity(conn)
        return _insert_friction(conn)

    bind_db = tmp_path / "cortex_dispatch_close.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    friction_id = seed_and_close_dispatch(cortex_db.cortex_conn())
    dispatch_body = _without_dispatch_envelope(
        execute_op("friction_close", {"assertion_id": friction_id, **body})
    )

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_close"
    )
    typed_id = seed_and_close_dispatch(cortex_db.cortex_conn())
    http_resp = typed_client.post(f"/frictions/{typed_id}/close", json=body)
    assert http_resp.status_code == 200, http_resp.text
    typed_body = _without_dispatch_envelope(http_resp.json())
    for key in ("fulfillment_assertion_id", "note_persisted", "promotion"):
        dispatch_body.pop(key, None)
        typed_body.pop(key, None)
    assert dispatch_body.get("status") == typed_body.get("status") == "closed"


@pytest.mark.offline
def test_observe_path_does_not_shadow_assertion_siblings(
    cortex_client: TestClient,
) -> None:
    search = cortex_client.get("/assertions/search", params={"q": "parity-shadow"})
    assert search.status_code != 422 or "int_parsing" not in search.text
    assert search.status_code == 200

    activate = cortex_client.get(
        "/assertions/activate",
        params={"entity_ids": "decision:shadow-probe"},
    )
    assert activate.status_code != 422 or "int_parsing" not in activate.text
    assert activate.status_code == 200


@pytest.mark.offline
def test_frictions_close_path_does_not_shadow_list_route(
    cortex_client: TestClient,
) -> None:
    listing = cortex_client.get("/frictions")
    assert listing.status_code == 200
    assert "int_parsing" not in listing.text
