"""Entity discoverability — default aliases and search/recall entity seeding (40072)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.dispatch_ops.ops_entities import _op_entity_create, _op_entity_update
from cortex_store.entity_discover import (
    entity_synthetic_assertion_id,
    mint_entity_create_aliases,
    search_entities_by_query,
)
from cortex_store.recall_resolve import _search_seed_entity_ids


@pytest.fixture()
def migrated_db(migrated_db_path, monkeypatch):
    from cortex_store import db

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    return migrated_db_path


def _dispatch_search(client: TestClient, query: str, *, limit: int = 5) -> dict:
    response = client.post(
        "/dispatch",
        json={"tool": "search", "arguments": {"query": query, "limit": limit}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mint_entity_create_aliases_merges_without_duplicating_caller(
    migrated_conn: sqlite3.Connection,
) -> None:
    aliases = mint_entity_create_aliases(
        "runbook:claude-ai-mcp-connect",
        "Claude AI MCP Connect",
        ["custom-alias"],
    )
    assert "custom-alias" in aliases
    assert "runbook:claude-ai-mcp-connect" in aliases
    assert "claude-ai-mcp-connect" in aliases
    assert "claude ai mcp connect" in aliases


def test_entity_create_writes_default_aliases(migrated_db) -> None:
    result = _op_entity_create(
        id="document:confirmation-wells-fargo",
        type="document",
        name="Confirmation - Wells Fargo",
        description="Wells Fargo payment confirmation",
        source_uri="cortex://evidence/2026-10-22_stub/Confirmation - Wells Fargo.pdf.extracted.md",
    )
    assert "error" not in result
    stored = result.get("aliases") or []
    assert "document:confirmation-wells-fargo" in stored
    assert "confirmation-wells-fargo" in stored


def test_entity_update_rename_mints_default_aliases(migrated_db) -> None:
    _op_entity_create(
        id="document:confirmation-wells-fargo",
        type="document",
        name="confirmation wells fargo",
        description="stem name only",
    )
    updated = _op_entity_update(
        entity_id="document:confirmation-wells-fargo",
        name="Confirmation - Wells Fargo",
    )
    assert "error" not in updated
    aliases = updated.get("aliases") or []
    assert "Confirmation - Wells Fargo" in aliases
    assert "confirmation-wells-fargo" in aliases


def test_search_wells_fargo_confirmation_ranks_document_first(
    cortex_client: TestClient,
    migrated_db,
) -> None:
    _op_entity_create(
        id="document:confirmation-wells-fargo",
        type="document",
        name="Confirmation - Wells Fargo",
        description="Wells Fargo payment confirmation",
        source_uri="cortex://evidence/2026-10-22_stub/Confirmation - Wells Fargo.pdf.extracted.md",
    )
    competing_claims = [
        "Wells Fargo confirmation code IB03QZRW3Z for scheduled payment",
        "Wells Fargo confirmation amount $252 minimum payment due",
        "Wells Fargo confirmation Autograph Visa ending 9062",
        "Wells Fargo confirmation everyday checking transfer",
        "Wells Fargo confirmation",
    ]
    for idx, claim in enumerate(competing_claims):
        noise_id = f"noise:wells-confirmation-{idx}"
        _op_entity_create(
            id=noise_id,
            type="note",
            name=f"Noise {idx}",
            description="competing fts row",
        )
        _op_assert(
            entity_id=noise_id,
            claim=claim,
            confidence="believed",
            evidence="unit test seed",
            derivation_type="direct_observation",
        )

    hits = _dispatch_search(
        cortex_client,
        "Wells Fargo confirmation",
        limit=5,
    )
    assert hits.get("items")
    first = hits["items"][0]
    assert first.get("entity_id") == "document:confirmation-wells-fargo"


def test_search_preserves_strong_assertion_hit_ranking(
    cortex_client: TestClient,
    migrated_db,
) -> None:
    _op_entity_create(
        id="todo:fts-search-projection-mode",
        type="todo",
        name="FTS projection",
        attributes={"density_triage": "mechanical"},
    )
    _op_assert(
        entity_id="todo:fts-search-projection-mode",
        claim="cortex projection mode summary intent keeps assertion hits stable",
        confidence="confirmed",
        evidence="unit test seed",
        derivation_type="direct_observation",
    )
    _op_entity_create(
        id="document:wells-noise",
        type="document",
        name="Wells partial overlap only",
        description="unrelated",
    )
    hits = _dispatch_search(
        cortex_client,
        "cortex projection mode summary intent",
        limit=3,
    )
    assert hits["items"][0]["entity_id"] == "todo:fts-search-projection-mode"


def test_recall_seeds_from_entity_fields_without_assertions(
    migrated_db,  # noqa: ARG001 — binds cortex_conn for search seed path
    migrated_conn: sqlite3.Connection,
) -> None:
    from cortex_store.entity_crud import create_entity_impl

    create_entity_impl(
        migrated_conn,
        {
            "id": "document:recall-alias-doc",
            "type": "document",
            "name": "Recall Alias Target",
            "description": "find me via alias seed",
        },
    )
    ids = _search_seed_entity_ids(migrated_conn, "recall alias target")
    assert ids == ["document:recall-alias-doc"]


def test_whole_token_search_rejects_substring_noise(
    migrated_conn: sqlite3.Connection,
) -> None:
    """Short queries must not match unrelated tokens embedded inside longer name tokens."""
    from cortex_store.entity_crud import create_entity_impl

    create_entity_impl(
        migrated_conn,
        {
            "id": "note:part-of-speech",
            "type": "note",
            "name": "part of speech",
            "description": "grammar reference",
        },
    )
    for query in ("art", "of"):
        hits = search_entities_by_query(migrated_conn, query, limit=5)
        assert all(hit["entity_id"] != "note:part-of-speech" for hit in hits)


def test_entity_synthetic_assertion_id_is_crc32_deterministic() -> None:
    """Synthetic search row ids must be stable across processes (zlib crc32, not hash())."""
    import zlib

    eid = "document:confirmation-wells-fargo"
    expected = -(abs(zlib.crc32(eid.encode("utf-8"))) % (2**30)) - 1
    assert entity_synthetic_assertion_id(eid) == expected
    assert entity_synthetic_assertion_id(eid) == expected
