"""Graph intelligence endpoints — impact analysis and spreading activation.

Kept separate from edges.py (CRUD) to isolate graph intelligence from
basic session-edge management.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..activation import spreading_activation
from ..belief_guard import analyze_assertion_impact
from ..db import cortex_conn
from ..db import query as db_query
from ..graph_utils import analyze_impact
from ..models import (
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    TouchedAssertionItem,
)

router = APIRouter(tags=["graph"])


@router.get("/edges/impact")
def impact_analysis(
    entity_id: str = Query(..., description="Seed entity for impact analysis"),
    depth: int = Query(2, ge=1, le=5, description="Max BFS depth"),
) -> dict[str, Any]:
    """Compute transitive dependency cascade from an entity.

    Follows **incoming** dependency edges (``requires``, ``depends_on``,
    ``derived_from``, ``evidence_for``, ``extends``) where the target is the
    seed, collecting the source as the impacted dependent —
    across BOTH the structural ``relationships`` and reasoning ``session_edges``
    substrates (cortex-spec §§8–9, migration 041).  Each impacted entity reports
    ``substrates`` (``structural`` = consensus ground truth, ``reasoning`` =
    session-attributed).  Useful when an assertion is superseded — shows what
    else might need revision.
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
                "substrates": ie.substrates,
            }
            for ie in result.impacted_entities
        ],
        "total_impacted_assertions": result.total_impacted_assertions,
    }


@router.post("/assertions/analyze-impact", response_model=ImpactAnalysisResponse)
def analyze_impact_semantic(
    body: ImpactAnalysisRequest,
) -> ImpactAnalysisResponse:
    """Semantic impact analysis — find assertions affected by a proposed claim.

    Uses entity-scoped hybrid search (FTS5 + vector) to identify assertions
    that may need revision if this claim is asserted. Exposes likely_supersedes
    for pre-write supersession guidance. Also available as an MCP tool.
    """
    conn = cortex_conn()
    try:
        entity_rows = db_query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entity_rows:
            raise HTTPException(
                status_code=404, detail=f"Entity not found: {body.entity_id}"
            )

        result = analyze_assertion_impact(
            conn, body.entity_id, body.claim, body.confidence
        )
    finally:
        conn.close()

    return ImpactAnalysisResponse(
        touched_assertions=[
            TouchedAssertionItem(
                assertion_id=t.assertion_id,
                claim=t.claim,
                confidence=t.confidence,
                similarity=t.similarity,
                entity_id=t.entity_id,
                retrieval_source=t.retrieval_source,
            )
            for t in result.touched_assertions
        ],
        likely_supersedes=result.likely_supersedes,
        implicated_entities=result.implicated_entities,
        impact_score=result.impact_score,
    )


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
                "substrates_traversed": a.substrates_traversed,
            }
            for a in result.activated
        ],
    }
