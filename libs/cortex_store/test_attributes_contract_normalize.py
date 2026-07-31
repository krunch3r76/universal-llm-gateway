"""Hermetic tests for attributes coerce-both contract (todo:cortex-attributes-contract-normalize)."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException, status

from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.dispatch_ops.ops_entities import _op_entity_create, _op_entity_update
from cortex_store.models import AssertionCreate, EntityCreate, EntityUpdate


def _entity_payload_422(result: dict[str, object]) -> None:
    assert result.get("status_code") == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = result.get("error")
    assert isinstance(detail, dict)
    assert detail["error"] == "entity_payload_invalid"
    assert detail["diagnostics"]


def test_entity_create_model_coerces_json_object_string() -> None:
    body = EntityCreate.model_validate(
        {
            "id": "todo:attrs-coerce",
            "type": "todo",
            "name": "Coerce test",
            "attributes": '{"priority":"low"}',
        }
    )
    assert body.attributes == {"priority": "low"}


def test_entity_update_model_coerces_json_object_string() -> None:
    body = EntityUpdate.model_validate({"attributes": '{"priority":"high"}'})
    assert body.attributes == {"priority": "high"}


def test_assertion_create_model_coerces_json_object_string() -> None:
    body = AssertionCreate.model_validate(
        {
            "entity_id": "todo:x",
            "claim": "claim",
            "confidence": "believed",
            "evidence": "test",
            "attributes": '{"stamp":"v1"}',
        }
    )
    assert body.attributes == {"stamp": "v1"}


@pytest.fixture()
def migrated_db(migrated_db_path, monkeypatch):
    from cortex_store import db

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    return migrated_db_path


def test_mcp_entity_create_json_string_attributes_succeeds(migrated_db) -> None:
    eid = "todo:attrs-create-string"
    result = _op_entity_create(
        id=eid,
        type="todo",
        name="String attrs",
        attributes='{"priority":"low","density_triage":"recon_pending"}',
    )
    assert "error" not in result
    assert result["id"] == eid
    assert result["attributes"] == {
        "priority": "low",
        "density_triage": "recon_pending",
    }


def test_mcp_entity_create_native_dict_attributes_succeeds(migrated_db) -> None:
    eid = "todo:attrs-create-dict"
    result = _op_entity_create(
        id=eid,
        type="todo",
        name="Dict attrs",
        attributes={"priority": "medium", "density_triage": "mechanical"},
    )
    assert "error" not in result
    assert result["attributes"] == {
        "priority": "medium",
        "density_triage": "mechanical",
    }


def test_mcp_entity_create_missing_density_triage_returns_422(migrated_db) -> None:
    result = _op_entity_create(
        id="todo:attrs-missing-triage",
        type="todo",
        name="Missing triage",
        attributes={"priority": "low"},
    )
    assert result.get("status_code") == 422
    err = result.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "density_triage_required"


def test_mcp_entity_create_garbage_string_attributes_returns_422(migrated_db) -> None:
    result = _op_entity_create(
        id="todo:attrs-create-bad",
        type="todo",
        name="Bad attrs",
        attributes="not-json",
    )
    _entity_payload_422(result)


def test_execute_op_entity_create_garbage_attributes_not_500(migrated_db) -> None:
    result = execute_op(
        "entity_create",
        {
            "id": "todo:attrs-exec-bad",
            "type": "todo",
            "name": "Exec bad attrs",
            "attributes": "not-json",
        },
    )
    _entity_payload_422(result)


def test_mcp_entity_update_json_string_attributes_merges(migrated_db) -> None:
    eid = "todo:attrs-update-string"
    create = _op_entity_create(
        id=eid,
        type="todo",
        name="Update string attrs",
        attributes={"existing": "keep", "density_triage": "recon_pending"},
    )
    assert "error" not in create

    updated = _op_entity_update(
        entity_id=eid,
        attributes='{"priority":"low"}',
    )
    assert "error" not in updated
    assert updated["attributes"] == {
        "existing": "keep",
        "density_triage": "recon_pending",
        "priority": "low",
    }


def test_mcp_entity_update_garbage_string_attributes_returns_422(migrated_db) -> None:
    eid = "todo:attrs-update-bad"
    create = _op_entity_create(
        id=eid,
        type="todo",
        name="Update bad attrs",
        attributes={"existing": "keep", "density_triage": "recon_pending"},
    )
    assert "error" not in create

    result = _op_entity_update(entity_id=eid, attributes="not-json")
    _entity_payload_422(result)

    from cortex_store.db import cortex_conn, query

    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT attributes FROM entities WHERE id = ?",
            (eid,),
        )
    stored = json.loads(rows[0]["attributes"])
    assert stored == {"existing": "keep", "density_triage": "recon_pending"}


def test_mcp_assert_json_string_attributes_succeeds(migrated_db) -> None:
    eid = "todo:attrs-assert-string"
    create = _op_entity_create(
        id=eid,
        type="todo",
        name="Assert string attrs",
        attributes={"density_triage": "recon_pending"},
    )
    assert "error" not in create

    result = _op_assert(
        entity_id=eid,
        claim="Attributes coerce on assert.",
        confidence="believed",
        evidence="unit test",
        derivation_type="inference",
        confidence_score=0.7,
        reasoning_summary="Hermetic attributes contract test.",
        attributes='{"stamp":"v1"}',
    )
    assert "error" not in result
    assert result["item"]["attributes"] == {"stamp": "v1"}


def test_mcp_assert_garbage_string_attributes_returns_422(migrated_db) -> None:
    eid = "todo:attrs-assert-bad"
    create = _op_entity_create(
        id=eid,
        type="todo",
        name="Assert bad attrs",
        attributes={"density_triage": "recon_pending"},
    )
    assert "error" not in create

    with pytest.raises(HTTPException) as exc_info:
        _op_assert(
            entity_id=eid,
            claim="Bad attributes should 422.",
            confidence="believed",
            evidence="unit test",
            derivation_type="inference",
            confidence_score=0.7,
            reasoning_summary="Hermetic attributes contract test.",
            attributes="not-json",
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "assertion_payload_invalid"
    assert detail["diagnostics"]
