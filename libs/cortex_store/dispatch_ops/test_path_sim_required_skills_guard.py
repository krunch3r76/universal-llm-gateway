"""a:27431 — path-sim must not seed into attributes.required_skills."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi import HTTPException

from cortex_store.dispatch_ops._path_sim_required_skills_guard import (
    _ERROR_CODE,
    reject_path_sim_in_required_skills,
)
from cortex_store.type_schemas import validate_distilled_attributes


def test_reject_path_sim_slug_fail_loud() -> None:
    with pytest.raises(HTTPException) as exc:
        reject_path_sim_in_required_skills(
            ["path-sim", "cheap-recon-before-escalation"]
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == _ERROR_CODE
    assert "todo_ulg.mdc" in exc.value.detail["message"]
    assert "a:27431" in exc.value.detail["message"]


def test_reject_agent_skill_prefixed_form() -> None:
    with pytest.raises(HTTPException) as exc:
        reject_path_sim_in_required_skills(["agent_skill:path-sim", "ulg-architecture"])
    assert exc.value.detail["error"] == _ERROR_CODE


def test_accepts_floor_skills_without_path_sim() -> None:
    reject_path_sim_in_required_skills(
        ["architecture-invariants", "ulg-architecture", "cheap-recon-before-escalation"]
    )


def test_ignores_non_list() -> None:
    reject_path_sim_in_required_skills(None)
    reject_path_sim_in_required_skills("path-sim")


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


def test_validate_distilled_attributes_rejects_path_sim_seed() -> None:
    """Codework seed carrying path-sim fails at the implement-lane chokepoint."""
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
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == _ERROR_CODE


def test_validate_distilled_attributes_accepts_floor_without_path_sim() -> None:
    c = _todo_conn()
    validate_distilled_attributes(
        c,
        "todo",
        {
            "required_skills": ["architecture-invariants", "ulg-architecture"],
        },
    )
