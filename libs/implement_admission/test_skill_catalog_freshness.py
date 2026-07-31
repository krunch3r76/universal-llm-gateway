"""Tests for skill catalog freshness probe (F1 / AC17)."""

from __future__ import annotations

import pytest

from claude_bundles.catalog import get_skill_catalog
from implement_admission.skill_catalog_resolver import catalog_source_uris
from implement_admission.skill_catalog_freshness import (
    check_catalog_valid,
    validate_generation_invariants,
)


@pytest.mark.offline
def test_catalog_freshness_gate_passes() -> None:
    assert check_catalog_valid() is None


@pytest.mark.offline
def test_generation_rejects_canonical_key_collision() -> None:
    entries = dict(catalog_source_uris())
    aliases = {
        "arch-alias-a": "architecture-invariants",
        "arch-alias-b": "architecture-invariants",
    }
    entries["arch-alias-a"] = "agent-skills/a.md"
    entries["arch-alias-b"] = "agent-skills/b.md"
    errors = validate_generation_invariants(entries, aliases=aliases)
    assert any("collision" in e for e in errors)


@pytest.mark.offline
def test_generation_rejects_alias_divergence() -> None:
    catalog = get_skill_catalog()
    entries = dict(catalog_source_uris())
    kernel_uri = entries["session-close-kernel"]
    entries["session-close"] = kernel_uri
    entries["session-close-kernel"] = "agent-skills/wrong.md"
    errors = validate_generation_invariants(
        entries, aliases=dict(catalog.alias_to_canonical)
    )
    assert errors
