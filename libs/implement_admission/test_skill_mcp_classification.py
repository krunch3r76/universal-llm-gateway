"""Catalog-backed MCP surface predicates."""

from __future__ import annotations

import pytest

from claude_bundles.catalog import clear_skill_catalog_cache
from implement_admission.skill_mcp_classification import (
    SkillClassificationMissingError,
    skill_mcp_predicated,
    skill_mcp_surface_required,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_skill_catalog_cache()
    yield
    clear_skill_catalog_cache()


def test_predicated_matches_non_none_surface() -> None:
    assert skill_mcp_predicated("fs") is True
    assert skill_mcp_surface_required("fs") == "life"
    assert skill_mcp_predicated("architecture-invariants") is False
    assert skill_mcp_surface_required("architecture-invariants") == "none"
    assert skill_mcp_surface_required("orchestrator-workflow") == "code"
    assert skill_mcp_predicated("rule:fs") is True


def test_missing_slug_fails_loud() -> None:
    with pytest.raises(SkillClassificationMissingError):
        skill_mcp_predicated("custom-skill-absent-from-classification")
