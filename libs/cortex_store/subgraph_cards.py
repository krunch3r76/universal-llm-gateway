"""Card v0 building + augmentation for subgraph rendering.

Internal helper module for :mod:`subgraph_renderer`. Wraps the canonical
:func:`card.get_entity_card` for bulk fetches and adds the
``description``/``status`` columns that Card v0 projection drops but the
spec markdown template needs.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .card import get_entity_card
from .db import json_decode, query


class CardBuildError(Exception):
    """A per-entity card build failed.

    Caught by the renderer and re-raised as a SubgraphRenderError so the
    public error surface stays uniform.
    """

    def __init__(self, entity_id: str, original: Exception) -> None:
        self.entity_id = entity_id
        self.original = original
        super().__init__(f"Card build failed for {entity_id}: {original}")


def build_cards(
    *,
    conn: sqlite3.Connection,
    visited_ids: list[str],
    top_k_assertions: int,
    include_superseded: bool,
) -> dict[str, dict[str, Any]]:
    """Build Card v0 payloads per visited entity.

    Passes ``source="boot"`` to bypass entity_access_log spam on the
    bulk BFS-scale read. TODO: extend ``get_entity_card`` to accept a
    dedicated ``"subgraph_render"`` source value once a second consumer
    needs this bypass.
    """
    cards: dict[str, dict[str, Any]] = {}
    for eid in visited_ids:
        try:
            cards[eid] = get_entity_card(
                conn, entity_id=eid, top_k=top_k_assertions, source="boot"
            )
        except Exception as exc:
            raise CardBuildError(eid, exc) from exc
    if include_superseded:
        _override_top_k_with_superseded(conn, cards, top_k_assertions)
    return cards


def augment_entity_columns(
    conn: sqlite3.Connection, visited_ids: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Batch-fetch ``description`` + ``status`` for the visited node set.

    Card v0 omits both; the V1.1 markdown template uses both. One extra
    batched query keeps the rendering faithful without expanding the
    Card v0 contract.
    """
    if not visited_ids:
        return {}, {}
    ph = ",".join("?" for _ in visited_ids)
    rows = query(
        conn,
        f"SELECT id, description, status FROM entities WHERE id IN ({ph})",
        tuple(visited_ids),
    )
    descriptions: dict[str, str] = {}
    statuses: dict[str, str] = {}
    for row in rows:
        eid = str(row["id"])
        descriptions[eid] = str(row["description"] or "")
        statuses[eid] = str(row["status"] or "")
    return descriptions, statuses


def _override_top_k_with_superseded(
    conn: sqlite3.Connection,
    cards: dict[str, dict[str, Any]],
    top_k_assertions: int,
) -> None:
    """Re-fetch top-K assertions including superseded rows.

    ``get_entity_card`` filters ``superseded_by IS NULL`` unconditionally;
    the render's ``include_superseded`` flag is scoped to assertion
    display per V1.1 spec, so we replace the card's top-K when set.
    """
    for eid in cards:
        rows = query(
            conn,
            "SELECT id, claim, confidence, derivation_type, valid_from, observed_at, evidence_uris "
            "FROM assertions WHERE entity_id = ? "
            "ORDER BY COALESCE(entrenchment_score,0) DESC, "
            "  COALESCE(observed_at,'') DESC, id DESC LIMIT ?",
            (eid, top_k_assertions),
        )
        cards[eid]["top_k_assertions"] = [
            {
                "id": int(row["id"]),
                "claim": row["claim"],
                "confidence": row["confidence"],
                "derivation_type": row.get("derivation_type"),
                "valid_from": row.get("valid_from"),
                "observed_at": row.get("observed_at"),
                "evidence_uris": json_decode(row.get("evidence_uris")),
            }
            for row in rows
        ]
