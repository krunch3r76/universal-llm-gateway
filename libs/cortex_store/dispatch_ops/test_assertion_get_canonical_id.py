"""assertion_get accepts canonical id prefixes (a:, assertion:) per spec."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops import execute_op
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get


@pytest.fixture()
def cortex_test_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Isolated DB with one assertion row; returns its numeric id."""
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        entity_id = "decision:assertion-get-canonical-id"
        insert_entity(conn, entity_id=entity_id, entity_type="decision")
        return insert_assertion(
            conn,
            entity_id=entity_id,
            claim="canonical assertion_get id fixture",
            confidence="confirmed",
        )


@pytest.mark.offline
@pytest.mark.parametrize("fmt", ["int", "digit_string", "a_prefix", "assertion_prefix"])
def test_assertion_get_accepts_canonical_id_forms(cortex_test_db: int, fmt: str) -> None:
    aid = cortex_test_db
    wire_id: int | str
    if fmt == "int":
        wire_id = aid
    elif fmt == "digit_string":
        wire_id = str(aid)
    elif fmt == "a_prefix":
        wire_id = f"a:{aid}"
    else:
        wire_id = f"assertion:{aid}"
    baseline = _op_assertion_get(assertion_id=aid)
    assert "error" not in baseline, baseline
    got = _op_assertion_get(assertion_id=wire_id)
    assert "error" not in got, got
    assert got["id"] == baseline["id"]
    assert got["claim"] == baseline["claim"]


@pytest.mark.offline
def test_assertion_get_dispatch_path_accepts_a_prefix(cortex_test_db: int) -> None:
    aid = cortex_test_db
    result = execute_op(
        "assertion_get",
        {"assertion_id": f"a:{aid}"},
    )
    assert "error" not in result, result
    assert result["id"] == aid


@pytest.mark.offline
@pytest.mark.parametrize(
    "wire_id",
    [999_999_999, "999999999", "a:999999999", "assertion:999999999"],
)
def test_assertion_get_missing_id_not_shape_error(
    cortex_test_db: int, migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch, wire_id
) -> None:
    _ = cortex_test_db
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    result = _op_assertion_get(assertion_id=wire_id)
    assert "error" in result
    assert result["error"].startswith("Assertion not found:")
    assert "invalid_assertion_id" not in str(result)
    assert result.get("status_code") is None


@pytest.mark.offline
@pytest.mark.parametrize(
    "wire_id",
    [
        "a:not-an-int",
        "assertion:",
        "assertion:abc",
        "garbage-id",
        "a:",
        "999_999_999",
    ],
)
def test_assertion_get_malformed_id_is_validation_not_absence(
    cortex_test_db: int, wire_id
) -> None:
    _ = cortex_test_db
    result = _op_assertion_get(assertion_id=wire_id)
    assert result.get("status_code") == 422
    err = result.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "invalid_assertion_id"
    assert "Assertion not found" not in str(result)


@pytest.mark.offline
@pytest.mark.parametrize(
    "wire_id",
    [
        "\u2074",
        "1\u00b2",
        "\u00b2\u00b3",
    ],
)
def test_assertion_get_unicode_digit_mismatch_is_shape_error(
    cortex_test_db: int, wire_id: str
) -> None:
    _ = cortex_test_db
    assert wire_id.isdigit()
    result = _op_assertion_get(assertion_id=wire_id)
    assert result.get("status_code") == 422
    err = result.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "invalid_assertion_id"
    assert "Assertion not found" not in str(result)


@pytest.mark.offline
@pytest.mark.parametrize(
    "wire_id",
    [
        9223372036854775808,
        "9223372036854775808",
        "a:9223372036854775808",
        "assertion:9223372036854775808",
    ],
)
def test_assertion_get_oversized_id_is_shape_error(
    cortex_test_db: int, wire_id: int | str
) -> None:
    _ = cortex_test_db
    result = _op_assertion_get(assertion_id=wire_id)
    assert result.get("status_code") == 422
    err = result.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "invalid_assertion_id"
    assert "Assertion not found" not in str(result)


@pytest.mark.offline
def test_assertion_get_dispatch_path_rejects_oversized_id(cortex_test_db: int) -> None:
    _ = cortex_test_db
    result = execute_op(
        "assertion_get",
        {"assertion_id": "9223372036854775808"},
    )
    assert result.get("status_code") == 422
    err = result.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "invalid_assertion_id"
