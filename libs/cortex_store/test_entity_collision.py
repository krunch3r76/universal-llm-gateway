"""Tests for entity_create embedding-similarity collision_warning (Tier B v1)."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.entity_collision import (
    attach_collision_warning,
    check_entity_collision,
)
from cortex_store.models.entities import EntityCollisionMatch, EntityCollisionWarning
from cortex_store.near_dup import DEDUP_SIMILARITY_THRESHOLD


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            workflow_state TEXT,
            aliases TEXT,
            attributes TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            confidence_band TEXT,
            lifecycle TEXT,
            adoption TEXT,
            confidence_score REAL,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, description, created_at, updated_at) "
        "VALUES ('todo:existing', 'todo', 'Ship collision warning', "
        "'Embedding similarity gate for entity_create', "
        "'2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z')"
    )
    conn.commit()
    return conn


def _vector_hit(
    *,
    entity_id: str,
    cosine_similarity: float,
    assertion_id: int = 1,
) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "entity_id": entity_id,
        "cosine_similarity": cosine_similarity,
        "distance": 1.0 - cosine_similarity,
    }


def test_no_match_below_threshold() -> None:
    conn = _conn()
    with (
        patch("cortex_store.entity_collision.cortex_embeddings.is_configured", return_value=True),
        patch("cortex_store.entity_collision.vector_store.is_initialized", return_value=True),
        patch(
            "cortex_store.entity_collision.cortex_embeddings.embed_query",
            return_value=[0.1, 0.2],
        ),
        patch(
            "cortex_store.entity_collision.vector_store.search_similar",
            return_value=[_vector_hit(entity_id="todo:existing", cosine_similarity=0.5)],
        ),
    ):
        warning = check_entity_collision(
            conn,
            entity_id="todo:new",
            entity_type="todo",
            name="Different topic",
            description="Unrelated work",
        )
    assert warning is None


def test_match_above_threshold_returned() -> None:
    conn = _conn()
    with (
        patch("cortex_store.entity_collision.cortex_embeddings.is_configured", return_value=True),
        patch("cortex_store.entity_collision.vector_store.is_initialized", return_value=True),
        patch(
            "cortex_store.entity_collision.cortex_embeddings.embed_query",
            return_value=[0.1, 0.2],
        ),
        patch(
            "cortex_store.entity_collision.vector_store.search_similar",
            return_value=[
                _vector_hit(entity_id="todo:existing", cosine_similarity=0.91, assertion_id=10),
                _vector_hit(entity_id="todo:existing", cosine_similarity=0.88, assertion_id=11),
            ],
        ),
    ):
        warning = check_entity_collision(
            conn,
            entity_id="todo:new",
            entity_type="todo",
            name="Ship collision warning",
            description="Embedding similarity gate for entity_create",
        )
    assert warning is not None
    assert warning.threshold == DEDUP_SIMILARITY_THRESHOLD
    assert len(warning.matches) == 1
    match = warning.matches[0]
    assert match.entity_id == "todo:existing"
    assert match.entity_type == "todo"
    assert match.name == "Ship collision warning"
    assert match.similarity == 0.91


def test_fail_open_when_embeddings_unavailable() -> None:
    conn = _conn()
    with patch(
        "cortex_store.entity_collision.cortex_embeddings.is_configured",
        return_value=False,
    ):
        assert (
            check_entity_collision(
                conn,
                entity_id="todo:new",
                entity_type="todo",
                name="X",
                description=None,
            )
            is None
        )

    with (
        patch("cortex_store.entity_collision.cortex_embeddings.is_configured", return_value=True),
        patch("cortex_store.entity_collision.vector_store.is_initialized", return_value=True),
        patch(
            "cortex_store.entity_collision.cortex_embeddings.embed_query",
            side_effect=RuntimeError("embedding down"),
        ),
    ):
        assert (
            check_entity_collision(
                conn,
                entity_id="todo:new",
                entity_type="todo",
                name="X",
                description=None,
            )
            is None
        )


def test_create_succeeds_with_collision_warning(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn",
        lambda: migrated_conn,
    )
    warning = EntityCollisionWarning(
        matches=[
            EntityCollisionMatch(
                entity_id="todo:existing",
                entity_type="todo",
                name="Existing",
                similarity=0.9,
            )
        ],
        threshold=DEDUP_SIMILARITY_THRESHOLD,
    )
    with patch(
        "cortex_store.dispatch_ops.ops_entities.check_entity_collision",
        return_value=warning,
    ):
        from cortex_store.dispatch_ops.ops_entities import _op_entity_create

        result = _op_entity_create(
            id="todo:brand-new",
            type="todo",
            name="Brand new",
            description="Fresh entity",
            attributes={"density_triage": "recon_pending"},
        )
    assert "error" not in result
    assert result["id"] == "todo:brand-new"
    assert result["collision_warning"]["matches"][0]["entity_id"] == "todo:existing"
    assert "collision_warning (advisory)" in result.get("_next", "")


def test_attach_collision_warning_sets_next_hint() -> None:
    result: dict[str, Any] = {"id": "todo:x"}
    warning = EntityCollisionWarning(
        matches=[
            EntityCollisionMatch(
                entity_id="todo:dup",
                entity_type="todo",
                name="Dup",
                similarity=0.87,
            )
        ],
        threshold=DEDUP_SIMILARITY_THRESHOLD,
    )
    attach_collision_warning(result, warning)
    assert result["collision_warning"]["matches"][0]["entity_id"] == "todo:dup"
    assert "todo:dup" in result["_next"]


def test_exact_slug_409_path_unchanged(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_entities.cortex_conn",
        lambda: migrated_conn,
    )
    from cortex_store.dispatch_ops.ops_entities import _op_entity_create

    first = _op_entity_create(
        id="todo:duplicate-slug",
        type="todo",
        name="First",
        description="seed",
        attributes={"density_triage": "recon_pending"},
    )
    assert "error" not in first
    with pytest.raises(HTTPException) as exc_info:
        _op_entity_create(
            id="todo:duplicate-slug",
            type="todo",
            name="Second",
            description="retry",
            attributes={"density_triage": "recon_pending"},
        )
    assert exc_info.value.status_code == 409
