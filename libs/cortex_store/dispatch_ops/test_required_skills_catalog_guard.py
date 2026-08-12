"""Arc 7098 P1 — uncatalogued required_skills rejected at write time."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.dispatch_ops._required_skills_catalog_guard import (
    _ERROR_CODE,
    reject_uncatalogued_required_skills,
)
from cortex_store.type_schemas import validate_distilled_attributes


def test_reject_skill_surface_rule_stem() -> None:
    with pytest.raises(HTTPException) as exc:
        reject_uncatalogued_required_skills(
            ["architecture-invariants", "skill-surface"]
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == _ERROR_CODE
    assert "skill-surface" in exc.value.detail["rejected_slugs"]
    assert "skill-surface" in exc.value.detail["message"]


def test_reject_testing_discipline_and_capability_dispatch() -> None:
    with pytest.raises(HTTPException) as exc:
        reject_uncatalogued_required_skills(
            ["testing-discipline", "capability-dispatch", "ulg-architecture"]
        )
    assert exc.value.detail["error"] == _ERROR_CODE
    rejected = exc.value.detail["rejected_slugs"]
    assert "testing-discipline" in rejected
    assert "capability-dispatch" in rejected


def test_reject_agent_skill_prefixed_uncatalogued() -> None:
    with pytest.raises(HTTPException) as exc:
        reject_uncatalogued_required_skills(["agent_skill:skill-surface"])
    assert "skill-surface" in exc.value.detail["rejected_slugs"]


def test_accepts_catalog_registered_floor() -> None:
    reject_uncatalogued_required_skills(
        ["architecture-invariants", "ulg-architecture", "advisor-timing"]
    )


def test_ignores_non_list() -> None:
    reject_uncatalogued_required_skills(None)
    reject_uncatalogued_required_skills("skill-surface")


def _todo_conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE type_attribute_schemas ("
        "entity_type TEXT PRIMARY KEY, required_keys TEXT, optional_keys TEXT, "
        "enum_constraints TEXT, notes TEXT)"
    )
    c.execute(
        "INSERT INTO type_attribute_schemas VALUES (?, ?, ?, ?, ?)",
        (
            "todo",
            json.dumps([]),
            json.dumps(
                ["files_expected", "acceptance_criteria", "required_skills", "priority"]
            ),
            json.dumps({}),
            None,
        ),
    )
    return c


def test_validate_distilled_attributes_rejects_skill_surface() -> None:
    c = _todo_conn()
    with pytest.raises(HTTPException) as exc:
        validate_distilled_attributes(
            c,
            "todo",
            {
                "required_skills": [
                    "architecture-invariants",
                    "ulg-architecture",
                    "skill-surface",
                ],
            },
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == _ERROR_CODE


def test_validate_distilled_attributes_accepts_catalog_floor() -> None:
    c = _todo_conn()
    validate_distilled_attributes(
        c,
        "todo",
        {
            "required_skills": ["architecture-invariants", "ulg-architecture"],
        },
    )


def test_path_sim_still_rejected_before_catalog() -> None:
    """a:27431 policy reject remains (path-sim is catalogued but banned as leaf)."""
    from cortex_store.dispatch_ops._path_sim_required_skills_guard import (
        _ERROR_CODE as PATH_SIM_CODE,
    )

    c = _todo_conn()
    with pytest.raises(HTTPException) as exc:
        validate_distilled_attributes(
            c,
            "todo",
            {
                "required_skills": [
                    "path-sim",
                    "architecture-invariants",
                    "ulg-architecture",
                ],
            },
        )
    assert exc.value.detail["error"] == PATH_SIM_CODE
