"""Graph intelligence endpoints — impact analysis and spreading activation.

Kept separate from edges.py (CRUD) to isolate graph intelligence from
basic session-edge management.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..activation import spreading_activation
from ..db import cortex_conn
from ..db import query as db_query
from ..graph_utils import analyze_impact

router = APIRouter(tags=["graph"])


@router.get("/edges/impact")
def impact_analysis(
    entity_id: str = Query(..., description="Seed entity for impact analysis"),
    depth: int = Query(2, ge=1, le=5, description="Max BFS depth"),
) -> dict[str, Any]:
    """Compute transitive dependency cascade from an entity.

    Follows outgoing ``depends_on``, ``evidence_for``, and ``extends`` edges
    to surface all downstream entities whose beliefs depend on the seed.
    Useful when an assertion is superseded — shows what else might need revision.
    """
    conn = cortex_conn()
    try:
        entity_rows = db_query(
            conn, "SELECT id FROM entities WHERE id = ?", (entity_id,)
        )
        if not entity_rows:
            raise HTTPException(
                status_code=404, detail=f"Entity not found: {entity_id}"
            )

        result = analyze_impact(conn, entity_id, depth)
    finally:
        conn.close()

    return {
        "seed_entity": result.seed_entity,
        "depth": result.depth,
        "impacted_entities": [
            {
                "entity_id": ie.entity_id,
                "entity_name": ie.entity_name,
                "hop_distance": ie.hop_distance,
                "path_trace": ie.path_trace,
                "assertion_count": ie.assertion_count,
                "edge_types": ie.edge_types,
            }
            for ie in result.impacted_entities
        ],
        "total_impacted_assertions": result.total_impacted_assertions,
    }


@router.get("/assertions/activate")
def activate(
    entity_ids: str = Query(..., description="Comma-separated seed entity IDs"),
    depth: int = Query(1, ge=1, le=3, description="Max walk depth"),
    max_results: int = Query(20, ge=1, le=100, description="Max assertions"),
    exclude_ids: str | None = Query(
        None, description="Comma-separated assertion IDs to exclude"
    ),
    suppress_hubs: bool = Query(True, description="Dampen high-degree hub entities"),
    decay_factor: float = Query(0.5, ge=0.0, le=1.0, description="Score decay per hop"),
) -> dict[str, Any]:
    """Spreading activation — walk the graph from seed entities to find related assertions.

    After hybrid search retrieves initial results, call this with the seed
    entity IDs to pull in structurally connected assertions that the query
    wouldn't find directly.  Scores by entrenchment × decay^hop with optional
    hub suppression for high-degree entities.
    """
    seeds = [s.strip() for s in entity_ids.split(",") if s.strip()]
    if not seeds:
        return {"seed_entities": [], "depth": depth, "activated": []}

    parsed_exclude: list[int] = []
    if exclude_ids:
        for eid in exclude_ids.split(","):
            eid = eid.strip()
            if eid.isdigit():
                parsed_exclude.append(int(eid))

    conn = cortex_conn()
    try:
        result = spreading_activation(
            conn,
            seeds,
            depth=depth,
            max_results=max_results,
            exclude_ids=parsed_exclude or None,
            decay_factor=decay_factor,
            suppress_hubs=suppress_hubs,
        )
        if seeds:
            try:
                conn.executemany(
                    "INSERT INTO entity_access_log "
                    "(entity_id, agent, operation, source) "
                    "VALUES (?, 'system', 'activate', 'activate')",
                    [(s,) for s in seeds],
                )
                conn.commit()
            except Exception:
                pass
    finally:
        conn.close()

    return {
        "seed_entities": result.seed_entities,
        "depth": result.depth,
        "hub_suppression": result.hub_suppression,
        "count": len(result.activated),
        "activated": [
            {
                "assertion_id": a.assertion_id,
                "entity_id": a.entity_id,
                "claim": a.claim,
                "confidence": a.confidence,
                "entrenchment_score": a.entrenchment_score,
                "activation_score": a.activation_score,
                "hop_distance": a.hop_distance,
                "activation_path": a.activation_path,
                "edge_types_traversed": a.edge_types_traversed,
            }
            for a in result.activated
        ],
    }
