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
# Close responses carry stable cross-path fields; only assertion row ids vary by store copy.
_VOLATILE_CLOSE_TOP_KEYS = frozenset(
    {"assertion_id", "fulfillment_assertion_id", "note_persisted"}
)

_ASSERTION_ROW_COLS = (
    "entity_id",
    "claim",
    "confidence",
    "evidence",
    "derivation_type",
    "superseded_by",
    "seeded_by",
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


def _normalize_close_result(body: dict[str, Any]) -> dict[str, Any]:
    """Full close payload minus envelope and store-local assertion ids."""
    body = _without_dispatch_envelope(body)
    out = {k: v for k, v in body.items() if k not in _VOLATILE_CLOSE_TOP_KEYS}
    if isinstance(out.get("item"), dict):
        out = copy.deepcopy(out)
        out["item"] = _normalize_item(out["item"])
    return out


def _assertion_row_snapshot(conn: sqlite3.Connection, assertion_id: int) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT {', '.join(_ASSERTION_ROW_COLS)} FROM assertions WHERE id = ?",
        (assertion_id,),
    ).fetchone()
    assert row is not None, f"missing assertion id={assertion_id}"
    return dict(zip(_ASSERTION_ROW_COLS, row, strict=True))


def _assertion_row_from_item(conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
    entity_id = item.get("entity_id")
    claim = item.get("claim")
    assert isinstance(claim, str)
    if isinstance(entity_id, str):
        row = conn.execute(
            f"SELECT {', '.join(_ASSERTION_ROW_COLS)} FROM assertions "
            "WHERE entity_id = ? AND claim = ? ORDER BY id DESC LIMIT 1",
            (entity_id, claim),
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT {', '.join(_ASSERTION_ROW_COLS)} FROM assertions "
            "WHERE claim = ? ORDER BY id DESC LIMIT 1",
            (claim,),
        ).fetchone()
    assert row is not None, f"no assertion for entity={entity_id!r} claim={claim!r}"
    return dict(zip(_ASSERTION_ROW_COLS, row, strict=True))


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
    persist_entity_id: str | None = None,
    persist_claim: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bind_db = tmp_path / "cortex_dispatch_write.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    if seed is not None:
        seed(cortex_db.cortex_conn())
    dispatch_raw = execute_op(op, dispatch_args)
    assert "error" not in dispatch_raw, dispatch_raw
    dispatch_body = _normalize_write_result(dispatch_raw)
    dispatch_row: dict[str, Any] | None = None
    if persist_entity_id is not None and persist_claim is not None:
        item = dispatch_raw.get("item") if isinstance(dispatch_raw.get("item"), dict) else {}
        claim = item.get("claim") if isinstance(item.get("claim"), str) else persist_claim
        entity = item.get("entity_id") if isinstance(item.get("entity_id"), str) else persist_entity_id
        dispatch_row = _assertion_row_from_item(
            cortex_db.cortex_conn(),
            {"entity_id": entity, "claim": claim},
        )

    typed_db = tmp_path / "cortex_typed_write.db"
    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_write"
    )
    if seed is not None:
        seed(cortex_db.cortex_conn())
    http_resp = typed_client.post(method_path, json=json_body)
    assert http_resp.status_code == 200, http_resp.text
    typed_body = _normalize_write_result(http_resp.json())
    assert dispatch_body == typed_body

    if dispatch_row is not None:
        typed_row = _assertion_row_from_item(
            cortex_db.cortex_conn(),
            {"entity_id": persist_entity_id, "claim": persist_claim},
        )
        assert dispatch_row == typed_row
    return dispatch_body, typed_body


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
        persist_entity_id=entity_id,
        persist_claim="S6 batch 2 observe parity fixture.",
    )


@pytest.mark.offline
def test_friction_dispatch_matches_typed_route(
    migrated_db_template: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_supersede_side_effects(monkeypatch)
    owner_id = "agent_skill:advisor-timing"

    def seed(conn: sqlite3.Connection) -> None:
        _seed_skill_entity(conn)

    note = "S6 batch 2 friction parity."
    args = {
        "owner": owner_id,
        "category": "tool_error",
        "note": note,
        "agent": "pytest",
        "actionable": False,
        "actionable_false_reason": "offline parity fixture — not commissioned work",
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
        persist_entity_id=owner_id,
        persist_claim=f"[tool_error] {note}",
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
        "evidence": "typed-route parity evidence",
    }

    def seed_friction(conn: sqlite3.Connection) -> int:
        _seed_skill_entity(conn)
        return _insert_friction(conn)

    bind_db = tmp_path / "cortex_dispatch_close.db"
    copy_template_db(migrated_db_template, bind_db)
    bind_cortex_db(monkeypatch, bind_db)
    friction_id = seed_friction(cortex_db.cortex_conn())
    dispatch_raw = execute_op("friction_close", {"assertion_id": friction_id, **body})
    dispatch_body = _normalize_close_result(dispatch_raw)

    typed_client = _isolated_client(
        migrated_db_template, tmp_path, monkeypatch, suffix="typed_close"
    )
    typed_db = tmp_path / "cortex_typed_close.db"
    typed_friction_id = seed_friction(cortex_db.cortex_conn())
    assert typed_friction_id == friction_id
    http_resp = typed_client.post(f"/frictions/{typed_friction_id}/close", json=body)
    assert http_resp.status_code == 200, http_resp.text
    typed_body = _normalize_close_result(http_resp.json())
    assert dispatch_body == typed_body

    fulfillment_id = dispatch_raw["fulfillment_assertion_id"]
    assert isinstance(fulfillment_id, int)
    bind_cortex_db(monkeypatch, bind_db)
    friction_row = _assertion_row_snapshot(cortex_db.cortex_conn(), friction_id)
    resolution_row = _assertion_row_snapshot(cortex_db.cortex_conn(), fulfillment_id)
    bind_cortex_db(monkeypatch, typed_db)
    friction_row_t = _assertion_row_snapshot(cortex_db.cortex_conn(), friction_id)
    resolution_row_t = _assertion_row_snapshot(cortex_db.cortex_conn(), fulfillment_id)
    assert friction_row == friction_row_t
    assert resolution_row == resolution_row_t
    assert friction_row["superseded_by"] == fulfillment_id
    assert "[resolved:wontfix]" in resolution_row["claim"]
    assert "parity close" in resolution_row["claim"]
    assert body["evidence"] in (resolution_row["evidence"] or "")


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
