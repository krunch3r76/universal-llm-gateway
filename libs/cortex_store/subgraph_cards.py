"""Card v0 building + augmentation for subgraph rendering.

Internal helper module for :mod:`subgraph_renderer`. Wraps the canonical
:func:`card.get_entity_card` for bulk fetches and adds the
``description``/``status`` columns that Card v0 projection drops but the
spec markdown template needs.

Under ``neighbor_fidelity=depth_aware``, sparse neighbors skip full Card
v0 assertion fetch — only root and hub-promoted nodes get ``get_entity_card``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .card import get_entity_card
from .card_adapters import get_adapter
from .db import json_decode, query
from .status_trait_read import synthesize_status_display
from .subgraph_neighbor_fidelity import (
    NeighborFidelity,
    card_top_k_for_entity,
    sparse_card_shell,
)


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
    root: str = "",
    visited: dict[str, int] | None = None,
    neighbor_fidelity: NeighborFidelity = "full",
    hub_rel_threshold: int = 20,
    rel_counts: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build Card v0 (or sparse projection) payloads per visited entity."""
    hop_map = visited or {eid: 0 for eid in visited_ids}
    rel_map = rel_counts or _batch_relationship_counts(conn, visited_ids)
    assn_map = _batch_assertion_counts(conn, visited_ids)
    entity_meta = _fetch_entity_meta(conn, visited_ids)

    cards: dict[str, dict[str, Any]] = {}
    for eid in visited_ids:
        hop = hop_map.get(eid, 0)
        rel_n = rel_map.get(eid, 0)
        top_k = card_top_k_for_entity(
            entity_id=eid,
            root=root,
            hop=hop,
            fidelity=neighbor_fidelity,
            top_k_root=top_k_assertions,
            hub_rel_threshold=hub_rel_threshold,
            rel_count=rel_n,
        )
        meta = entity_meta.get(eid, {})
        try:
            if neighbor_fidelity == "full" or top_k > 0:
                cards[eid] = get_entity_card(
                    conn, entity_id=eid, top_k=top_k, source="boot"
                )
            else:
                cards[eid] = sparse_card_shell(
                    entity_id=eid,
                    entity_type=str(meta.get("type", "")),
                    name=str(meta.get("name", eid)),
                    active_assertion_count=assn_map.get(eid, 0),
                    rel_count=rel_n,
                )
        except Exception as exc:
            raise CardBuildError(eid, exc) from exc

    if include_superseded:
        for eid in visited_ids:
            top_k = card_top_k_for_entity(
                entity_id=eid,
                root=root,
                hop=hop_map.get(eid, 0),
                fidelity=neighbor_fidelity,
                top_k_root=top_k_assertions,
                hub_rel_threshold=hub_rel_threshold,
                rel_count=rel_map.get(eid, 0),
            )
            if top_k > 0:
                _override_top_k_with_superseded(conn, cards, top_k, eid)
    _attach_sparse_hub_summaries(cards, entity_meta, rel_map, hub_rel_threshold)
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
    table_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
    }
    select_cols = ["id", "description"]
    for col in ("lifecycle", "confidence_band", "adoption"):
        if col in table_cols:
            select_cols.append(col)
    ph = ",".join("?" for _ in visited_ids)
    rows = query(
        conn,
        f"SELECT {', '.join(select_cols)} FROM entities WHERE id IN ({ph})",
        tuple(visited_ids),
    )
    descriptions: dict[str, str] = {}
    statuses: dict[str, str] = {}
    for row in rows:
        eid = str(row["id"])
        descriptions[eid] = str(row["description"] or "")
        display = synthesize_status_display(row)
        statuses[eid] = display if display is not None else ""
    return descriptions, statuses


def _fetch_entity_meta(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not entity_ids:
        return {}
    ph = ",".join("?" for _ in entity_ids)
    rows = query(
        conn,
        f"SELECT id, type, name, description FROM entities WHERE id IN ({ph})",
        tuple(entity_ids),
    )
    return {str(row["id"]): dict(row) for row in rows}


def _batch_assertion_counts(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, int]:
    if not entity_ids:
        return {}
    ph = ",".join("?" for _ in entity_ids)
    rows = query(
        conn,
        f"SELECT entity_id, COUNT(*) AS n FROM assertions "
        f"WHERE entity_id IN ({ph}) AND superseded_by IS NULL GROUP BY entity_id",
        tuple(entity_ids),
    )
    return {str(row["entity_id"]): int(row["n"]) for row in rows}


def _batch_relationship_counts(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, int]:
    if not entity_ids:
        return {}
    ph = ",".join("?" for _ in entity_ids)
    rows = query(
        conn,
        f"SELECT entity_id, COUNT(*) AS n FROM ("
        f"  SELECT from_entity AS entity_id FROM relationships "
        f"  WHERE from_entity IN ({ph}) AND active = 1 AND valid_until IS NULL "
        f"  UNION ALL "
        f"  SELECT to_entity AS entity_id FROM relationships "
        f"  WHERE to_entity IN ({ph}) AND active = 1 AND valid_until IS NULL"
        f") GROUP BY entity_id",
        tuple(entity_ids + entity_ids),
    )
    return {str(row["entity_id"]): int(row["n"]) for row in rows}


def _attach_sparse_hub_summaries(
    cards: dict[str, dict[str, Any]],
    entity_meta: dict[str, dict[str, Any]],
    rel_counts: dict[str, int],
    hub_rel_threshold: int,
) -> None:
    for eid, card in cards.items():
        if card.get("top_k_assertions"):
            continue
        rel_n = rel_counts.get(eid, 0)
        if rel_n < hub_rel_threshold:
            continue
        meta = entity_meta.get(eid, {})
        adapter = get_adapter(str(meta.get("type", "")))
        summary = adapter.summary_row(meta)
        if summary:
            card["summary_row"] = str(summary)[:120]


def _override_top_k_with_superseded(
    conn: sqlite3.Connection,
    cards: dict[str, dict[str, Any]],
    top_k_assertions: int,
    entity_id: str,
) -> None:
    """Re-fetch top-K assertions including superseded rows for one entity."""
    rows = query(
        conn,
        "SELECT id, claim, confidence, derivation_type, valid_from, observed_at, evidence_uris "
        "FROM assertions WHERE entity_id = ? "
        "ORDER BY COALESCE(entrenchment_score,0) DESC, "
        "  COALESCE(observed_at,'') DESC, id DESC LIMIT ?",
        (entity_id, top_k_assertions),
    )
    cards[entity_id]["top_k_assertions"] = [
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
