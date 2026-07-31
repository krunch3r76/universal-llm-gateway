"""Regression tests for rules/skills corpus migration helpers (arc 3924 step-1)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from cortex_store.routes._skill_index import LAYER_ENTITY_TYPES, _decode_related_skills
from cortex_store.routes._skill_suggest_candidates import norm_loaded
from cortex_store.seat_applicability import validate_scope


def test_layer_entity_types_widen_skills_and_all() -> None:
    assert LAYER_ENTITY_TYPES["skills"] == ("agent_skill", "skill")
    assert LAYER_ENTITY_TYPES["all"] == ("agent_skill", "rule", "skill")


def test_decode_related_skills_strips_three_prefixes() -> None:
    assert _decode_related_skills(
        '["agent_skill:foo", "rule:bar", "skill:baz"]'
    ) == ["foo", "bar", "baz"]


def test_norm_loaded_strips_three_prefixes() -> None:
    assert norm_loaded("rule:dispatch-shape") == "dispatch-shape"
    assert norm_loaded("skill:implement-work-item") == "implement-work-item"


def test_validate_scope_rejects_unknown_token() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_scope({"scope": "bogus"})
    assert exc.value.status_code == 422


def test_validate_scope_accepts_closed_enum() -> None:
    validate_scope({"scope": "ecosystem"})
