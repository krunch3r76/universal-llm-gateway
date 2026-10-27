"""Hermetic tests for default entity_update receipt vs echo=card."""

from __future__ import annotations

import json

import pytest

from cortex_store.dispatch_ops.ops_entities import _op_entity_create, _op_entity_update


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
    assert "entity_get" in receipt["card_call"]
    receipt_bytes = len(
        json.dumps(receipt, separators=(",", ":")).encode("utf-8")
    )
    assert receipt_bytes <= 1024

    card = _op_entity_update(entity_id=eid, description="still long", echo="card")
    assert "error" not in card
    assert "description" in card
    assert "assertions" in card
    assert card["description"] == "still long"
    assert len(json.dumps(card).encode("utf-8")) > 1024
