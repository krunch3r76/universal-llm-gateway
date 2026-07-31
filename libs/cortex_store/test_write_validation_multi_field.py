"""Multi-field 422 batching for cortex dispatch write ops (idle-wake 6590)."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops.ops_assertions_write import _op_assert, _op_friction_close
from cortex_store.dispatch_ops.ops_entities import _op_entity_create


def test_entity_create_empty_returns_all_top_level_missing() -> None:
    result = _op_entity_create()
    assert result["status_code"] == 422
    err = result["error"]
    assert isinstance(err, dict)
    assert err["error"] == "missing_required_fields"
    fields = {e["field"] for e in err["errors"]}
    assert fields == {"id", "type", "name"}


def test_entity_create_todo_missing_id_and_density_triage(migrated_db) -> None:
    result = _op_entity_create(
        type="todo",
        name="Wake test",
        attributes={},
    )
    assert result["status_code"] == 422
    err = result["error"]
    assert isinstance(err, dict)
    assert err["error"] == "missing_required_fields"
    by_field = {e["field"]: e for e in err["errors"]}
    assert "id" in by_field
    assert "attributes.density_triage" in by_field
    assert "accepted" in by_field["attributes.density_triage"]
    assert "mechanical" in by_field["attributes.density_triage"]["accepted"]


def test_entity_create_aliases_entity_id_entity_type_title(migrated_db) -> None:
    result = _op_entity_create(
        entity_id="todo:alias-wake",
        entity_type="todo",
        title="Alias mint",
        attributes={"density_triage": "recon_pending"},
    )
    assert "error" not in result
    assert result["id"] == "todo:alias-wake"


def test_friction_close_missing_both_required_fields() -> None:
    result = _op_friction_close()
    assert result["status_code"] == 422
    err = result["error"]
    assert isinstance(err, dict)
    assert err["error"] == "missing_required_fields"
    by_field = {e["field"]: e for e in err["errors"]}
    assert set(by_field) == {"assertion_id", "resolution_kind"}
    assert "accepted" in by_field["resolution_kind"]
    assert "commit:{sha}" in by_field["resolution_kind"]["accepted"]


def test_assert_missing_multiple_required_fields() -> None:
    result = _op_assert(entity_id="todo:x")
    assert result["status_code"] == 422
    err = result["error"]
    assert isinstance(err, dict)
    assert err["error"] == "missing_required_fields"
    fields = {e["field"] for e in err["errors"]}
    assert fields == {"claim", "confidence", "evidence"}


@pytest.fixture()
def migrated_db(migrated_db_path, monkeypatch):
    from cortex_store import db

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    return migrated_db_path
