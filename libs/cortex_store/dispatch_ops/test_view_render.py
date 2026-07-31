"""Tests for view_render op — register, refresh, archive, typed errors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.dispatch_ops import _shared as dispatch_shared
from cortex_store.dispatch_ops._views.recipe import load_recipe, validate_recipe
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.dispatch_ops.ops_relationships import _op_relationship_create
from cortex_store.dispatch_ops.ops_views import _op_view_render


@pytest.fixture()
def view_env(migrated_db_path, tmp_path, monkeypatch):
    from cortex_store.conftest import bind_cortex_db

    files_root = tmp_path / "files"
    files_root.mkdir()
    bind_cortex_db(monkeypatch, migrated_db_path)
    monkeypatch.setattr(dispatch_shared, "_FILES_ROOT", files_root)
    monkeypatch.setattr("cortex_store.dispatch_ops.ops_views._FILES_ROOT", files_root)
    return {"files_root": files_root}


def _seed_case_and_doc(view_env: dict) -> tuple[str, str]:
    root = _op_entity_create(id="case:test-view", type="case", name="Test View Case")
    assert "error" not in root
    doc = _op_entity_create(
        id="document:test-charter",
        type="document",
        name="Test Charter",
        source_uri="cortex://notes/views/test-charter.md",
    )
    assert "error" not in doc
    return "case:test-view", "document:test-charter"


@pytest.mark.offline
def test_recipes_load_and_validate() -> None:
    for profile in ("matter_charter", "matter_doctrine", "matter_index"):
        recipe = load_recipe(profile)
        validate_recipe(recipe, expected_profile=profile, expected_version=1)


def test_register_refresh_happy_path(view_env: dict) -> None:
    root_id, doc_id = _seed_case_and_doc(view_env)
    reg = _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="matter_charter",
        agent="cursor",
        session_id="cursor-2026-07-12-1200-abc",
    )
    assert "error" not in reg, reg
    assert reg["view_rev"] == 1
    assert reg["core_hash"].startswith("sha256:")

    rel = _op_relationship_create(
        source_id=doc_id, target_id=root_id, type_id="derived_from"
    )
    assert "error" not in rel or rel.get("id") is not None

    refresh = _op_view_render(document_id=doc_id, mode="refresh")
    assert "error" not in refresh, refresh
    assert refresh["sections_repaired"] == []


def test_idempotent_refresh_no_rev_bump(view_env: dict) -> None:
    root_id, doc_id = _seed_case_and_doc(view_env)
    _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="matter_charter",
    )
    first = _op_view_render(document_id=doc_id, mode="refresh")
    second = _op_view_render(document_id=doc_id, mode="refresh")
    assert first["core_hash"] == second["core_hash"]
    assert second["view_rev"] == first["view_rev"]


def test_typed_errors(view_env: dict) -> None:
    missing = _op_view_render(document_id="document:missing", mode="register")
    assert missing.get("code") == "document_not_found"

    root_id, doc_id = _seed_case_and_doc(view_env)
    no_root = _op_view_render(
        document_id=doc_id,
        mode="register",
        view_profile="matter_charter",
    )
    assert no_root.get("code") == "view_root_required"

    bad_profile = _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="unknown_profile",
    )
    assert bad_profile.get("code") == "unknown_view_profile"

    unreg = _op_view_render(document_id=doc_id, mode="refresh")
    assert unreg.get("code") == "view_not_registered"

    asof_valid = _op_view_render(document_id=doc_id, as_of_valid="2026-01-01T00:00:00Z")
    assert asof_valid.get("code") == "as_of_valid_unsupported"


def test_anti_amnesia_violation(view_env: dict) -> None:
    root_id, doc_id = _seed_case_and_doc(view_env)
    _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="matter_charter",
        narrative_sections={
            "narrative_layer": "Synthesis cites [assertion:1] for grounding."
        },
    )
    result = _op_view_render(
        document_id=doc_id,
        mode="refresh",
        narrative_sections={"narrative_layer": "Changed without delta [assertion:2]"},
    )
    assert result.get("code") == "anti_amnesia_violation"


def test_citation_grammar_violation(view_env: dict) -> None:
    root_id, doc_id = _seed_case_and_doc(view_env)
    result = _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="matter_charter",
        narrative_sections={"narrative_layer": "No citation here."},
    )
    assert result.get("code") == "citation_grammar_violation"


def test_index_profile_without_root(view_env: dict) -> None:
    doc = _op_entity_create(
        id="document:test-index",
        type="document",
        name="Test Index",
        source_uri="cortex://notes/views/test-index.md",
    )
    assert "error" not in doc
    reg = _op_view_render(
        document_id="document:test-index",
        mode="register",
        view_profile="matter_index",
    )
    assert "error" not in reg, reg


def test_read_asof_round_trip(view_env: dict) -> None:
    root_id, doc_id = _seed_case_and_doc(view_env)
    reg = _op_view_render(
        document_id=doc_id,
        mode="register",
        root_id=root_id,
        view_profile="matter_charter",
    )
    assert "error" not in reg
    full = _op_view_render(document_id=doc_id, mode="full")
    assert "error" not in full
    assert full["archived_revision_uri"]
    stamp = reg.get("stamp") or {}
    as_of = (stamp.get("time") or "2026-07-12T12:00:00+00:00")
    read = _op_view_render(
        document_id=doc_id,
        mode="read_asof",
        as_of_system=as_of,
    )
    assert "error" not in read or read.get("code") == "as_of_instance_not_found"
