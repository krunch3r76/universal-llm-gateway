"""Unit tests for libs/cortex_store/type_schemas.py.

Covers the contract documented in spec § 1.1 / § 1.2 / § 1.3 / § 4.1
and enforced by ``validate_required_attributes``:

  * Types not registered in type_attribute_schemas pass validation
    unconditionally (free-form attributes contract).
  * Missing required attributes raise 422 with a structured detail.
  * Enum-constrained attributes reject out-of-allowlist values with 422.
  * Optional attributes are allowed but not required.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.type_schemas import (
    type_attribute_schema,
    validate_distilled_attributes,
    validate_required_attributes,
)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE type_attribute_schemas ("
        "  entity_type TEXT PRIMARY KEY,"
        "  required_keys TEXT NOT NULL,"
        "  optional_keys TEXT NOT NULL,"
        "  enum_constraints TEXT NOT NULL,"
        "  notes TEXT"
        ")"
    )
    c.execute(
        "INSERT INTO type_attribute_schemas VALUES (?, ?, ?, ?, ?)",
        (
            "legal_source",
            json.dumps(
                [
                    "citation_canonical",
                    "citation_short",
                    "authority_class",
                    "jurisdiction",
                ]
            ),
            json.dumps(["effective_date", "aliases"]),
            json.dumps({"authority_class": ["statute", "regulation", "probate_code"]}),
            None,
        ),
    )
    return c


def test_unregistered_type_passes() -> None:
    c = _conn()
    validate_required_attributes(c, "person", {"name": "x"})
    validate_required_attributes(c, "person", None)


def test_missing_required_raises_422() -> None:
    c = _conn()
    with pytest.raises(HTTPException) as exc:
        validate_required_attributes(
            c,
            "legal_source",
            {"citation_canonical": "x"},
        )
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert detail["error"] == "type_attribute_required_missing"
    assert set(detail["missing"]) == {
        "citation_short",
        "authority_class",
        "jurisdiction",
    }


def test_all_required_passes() -> None:
    c = _conn()
    validate_required_attributes(
        c,
        "legal_source",
        {
            "citation_canonical": "Cal. R&T § 63.2",
            "citation_short": "§ 63.2",
            "authority_class": "statute",
            "jurisdiction": "CA",
        },
    )


def test_enum_violation_raises_422() -> None:
    c = _conn()
    with pytest.raises(HTTPException) as exc:
        validate_required_attributes(
            c,
            "legal_source",
            {
                "citation_canonical": "x",
                "citation_short": "x",
                "authority_class": "BLOG_POST",
                "jurisdiction": "CA",
            },
        )
    assert exc.value.status_code == 422
    detail = exc.value.detail
    assert detail["error"] == "type_attribute_enum_violation"
    assert detail["violations"][0]["attribute"] == "authority_class"
    assert detail["violations"][0]["value"] == "BLOG_POST"


def test_type_attribute_schema_round_trip() -> None:
    c = _conn()
    schema = type_attribute_schema(c, "legal_source")
    assert schema is not None
    assert "citation_canonical" in schema["required"]
    assert "effective_date" in schema["optional"]
    assert "statute" in schema["enums"]["authority_class"]
    assert type_attribute_schema(c, "unregistered_type") is None


def test_validation_no_op_when_registry_table_absent() -> None:
    """Pre-migration sandboxes / fresh DBs degrade to free-form attributes."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    assert type_attribute_schema(c, "legal_source") is None
    validate_required_attributes(c, "legal_source", {})
    validate_required_attributes(c, "legal_source", None)


def _todo_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE type_attribute_schemas ("
        "  entity_type TEXT PRIMARY KEY,"
        "  required_keys TEXT NOT NULL,"
        "  optional_keys TEXT NOT NULL,"
        "  enum_constraints TEXT NOT NULL,"
        "  notes TEXT"
        ")"
    )
    c.execute(
        "INSERT INTO type_attribute_schemas VALUES (?, ?, ?, ?, ?)",
        (
            "todo",
            json.dumps([]),
            json.dumps(
                [
                    "files_expected",
                    "acceptance_criteria",
                    "required_skills",
                    "density_triage",
                ]
            ),
            json.dumps({}),
            None,
        ),
    )
    return c


def test_distilled_attributes_accepts_well_formed() -> None:
    c = _todo_conn()
    validate_distilled_attributes(
        c,
        "todo",
        {
            "files_expected": ["libs/a.py"],
            "acceptance_criteria": ["AC one"],
            "required_skills": ["architecture-invariants"],
            "priority": "high",
        },
    )


def test_distilled_attributes_no_op_without_lane_keys() -> None:
    c = _todo_conn()
    validate_distilled_attributes(c, "todo", {"priority": "high"})


@pytest.mark.parametrize(
    ("attrs", "error"),
    [
        ({"files_expected": []}, "implement_attr_shape_invalid"),
        ({"acceptance_criteria": "x"}, "implement_attr_shape_invalid"),
        ({"required_skills": [" "]}, "implement_attr_shape_invalid"),
        ({"files_modified": ["a.py"]}, "implement_attr_alias_rejected"),
        ({"acceptance": ["done"]}, "implement_attr_alias_rejected"),
    ],
)
def test_distilled_attributes_rejects_bad_shape_or_alias(
    attrs: dict, error: str
) -> None:
    c = _todo_conn()
    with pytest.raises(HTTPException) as exc:
        validate_distilled_attributes(c, "todo", attrs)
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == error
