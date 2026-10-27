"""Hermetic tests for default entity_update receipt vs echo=card."""

from __future__ import annotations

import json

import pytest

from cortex_store.db import cortex_conn, json_encode
from cortex_store.dispatch_ops.entity_update_receipt import entity_detail_sha256
from cortex_store.dispatch_ops.ops_entities import _op_entity_create, _op_entity_update
from cortex_store.entity_read import get_entity_impl


@pytest.fixture()
def migrated_db(migrated_db_path, monkeypatch):
    from cortex_store import db

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    return migrated_db_path


def _long_description(n: int = 4000) -> str:
    return "x" * n


def test_entity_update_default_receipt_small_and_echo_card_full(migrated_db) -> None:
    eid = "todo:receipt-echo-40456"
    attrs = {f"key_{i}": f"value_{i}" for i in range(40)}
    create = _op_entity_create(
        id=eid,
        type="todo",
        name="Receipt leg",
        description=_long_description(),
        attributes={**attrs, "density_triage": "mechanical"},
    )
    assert "error" not in create

    receipt = _op_entity_update(entity_id=eid, notes="patched")
    assert "error" not in receipt
    assert set(receipt.keys()) == {
        "id",
        "updated_at",
        "sha256",
        "changed_keys",
        "card_call",
    }
    assert receipt["id"] == eid
    assert receipt["changed_keys"] == ["notes"]
    assert len(receipt["sha256"]) == 64
    assert receipt["card_call"] == (
        f'cortex(tool="entity_get", arguments=\'{{"entity_id":"{eid}"}}\')'
    )
    receipt_bytes = len(
        json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    )
    assert receipt_bytes <= 1024

    with cortex_conn() as conn:
        full = get_entity_impl(conn, entity_id=eid, source="boot")
    assert receipt["sha256"] == entity_detail_sha256(full)

    card = _op_entity_update(entity_id=eid, description="still long", echo="card")
    assert "error" not in card
    assert "description" in card
    assert "assertions" in card
    assert card["description"] == "still long"
    with cortex_conn() as conn:
        full_after = get_entity_impl(conn, entity_id=eid, source="boot")
    assert entity_detail_sha256(card) == entity_detail_sha256(full_after)
    assert len(json.dumps(card).encode("utf-8")) > 1024


def test_entity_update_receipt_includes_endeavor_birth_warning(migrated_db) -> None:
    eid = "opportunity:receipt-birth-warn"
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, attributes, created_at, updated_at) "
            "VALUES (?, 'opportunity', ?, ?, datetime('now'), datetime('now'))",
            (eid, eid, json_encode({"mode": "endeavor"})),
        )
        conn.commit()

    receipt = _op_entity_update(entity_id=eid, notes="touch")
    assert "error" not in receipt
    assert "warnings" in receipt
    assert any(w.get("kind") == "endeavor_birth_incomplete" for w in receipt["warnings"])

    card = _op_entity_update(entity_id=eid, notes="touch2", echo="card")
    assert "error" not in card
    assert card.get("endeavor_birth_warning", {}).get("kind") == "endeavor_birth_incomplete"


def test_entity_update_unknown_entity_error_not_receipt(migrated_db) -> None:
    result = _op_entity_update(entity_id="todo:missing-40456-receipt", notes="nope")
    assert "error" in result
    assert "card_call" not in result
    assert "sha256" not in result
    assert "changed_keys" not in result
