"""Embedding-similarity collision warning for entity_create (Tier B v1).

WARN-only — never blocks or rolls back creates. Fail-open when embeddings or
the vector store are unavailable (same posture as belief_guard hybrid fallback).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from universal_logging import get_logger

from . import embeddings as cortex_embeddings
from . import vector_store
from .db import query
from .models.entities import EntityCollisionMatch, EntityCollisionWarning
from .near_dup import DEDUP_SIMILARITY_THRESHOLD

logger = get_logger("cortex-api.entity-collision")

_COLLISION_TOP_K = 5
_SEARCH_N_RESULTS = 80
_COMPOSITE_TEXT_CAP = 512


def _composite_text(
    entity_type: str,
    name: str,
    description: str | None,
) -> str:
    desc_part = f". {description}" if description else ""
    text = f"{entity_type}: {name}{desc_part}"
    return text[:_COMPOSITE_TEXT_CAP]


def check_entity_collision(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    description: str | None,
) -> EntityCollisionWarning | None:
    """Return semantic near-duplicates above threshold, or None."""
    if not cortex_embeddings.is_configured() or not vector_store.is_initialized():
        return None
    try:
        text = _composite_text(entity_type, name, description)
        embedding = cortex_embeddings.embed_query(text)
        raw = vector_store.search_similar(embedding, n_results=_SEARCH_N_RESULTS)
    except Exception:
        logger.warning(
            "entity_create collision check failed — proceeding without warning",
            exc_info=True,
        )
        return None

    max_by_entity: dict[str, float] = {}
    for item in raw:
        candidate_id = item.get("entity_id")
        if not candidate_id or not isinstance(candidate_id, str):
            continue
        if candidate_id == entity_id:
            continue
        sim = float(item.get("cosine_similarity") or 0.0)
        if sim >= DEDUP_SIMILARITY_THRESHOLD:
            prev = max_by_entity.get(candidate_id, 0.0)
            if sim > prev:
                max_by_entity[candidate_id] = sim

    if not max_by_entity:
        return None

    ranked = sorted(max_by_entity.items(), key=lambda pair: pair[1], reverse=True)[
        :_COLLISION_TOP_K
    ]
    entity_ids = [eid for eid, _ in ranked]
    placeholders = ", ".join("?" * len(entity_ids))
    rows = query(
        conn,
        f"SELECT id, type, name FROM entities WHERE id IN ({placeholders})",
        tuple(entity_ids),
    )
    row_by_id = {str(row["id"]): row for row in rows}

    matches: list[EntityCollisionMatch] = []
    for candidate_id, sim in ranked:
        row = row_by_id.get(candidate_id)
        matches.append(
            EntityCollisionMatch(
                entity_id=candidate_id,
                entity_type=str(row["type"]) if row else None,
                name=str(row["name"]) if row else None,
                similarity=round(sim, 4),
            )
        )

    return EntityCollisionWarning(
        matches=matches,
        threshold=DEDUP_SIMILARITY_THRESHOLD,
    )


def attach_collision_warning(
    result: dict[str, Any],
    warning: EntityCollisionWarning,
) -> None:
    """Merge optional collision_warning into a successful create payload."""
    payload = warning.model_dump(mode="json")
    result["collision_warning"] = payload
    top = warning.matches[0] if warning.matches else None
    if top is None:
        return
    hint = (
        f"collision_warning (advisory): semantic near-duplicate "
        f"{top.entity_id!r} (sim={top.similarity:.2f})"
    )
    if "_next" in result:
        result["_next"] = f"{result['_next']}; {hint}"
    else:
        result["_next"] = hint
