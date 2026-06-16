"""Subgraph render + walk routes — thin FastAPI wrappers over shared modules.

Unprefixed ``/subgraph/render`` and ``/subgraph/walk`` per cortex-api
convention (internal-only; external mapping to ``/api/v1/*`` is a
Stargate-side concern, separate PR).

Validation lives entirely in :func:`subgraph_renderer.render_subgraph` and
:func:`subgraph_walker.walk_subgraph` — the routes do NOT pre-validate.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..db import cortex_conn
from ..dispatch_ops.ops_subgraph import (
    _serialize_render_result,
    _serialize_walk_result,
)
from ..subgraph_renderer import SubgraphRenderError, render_subgraph
from ..subgraph_walker import SubgraphWalkError, walk_subgraph

router = APIRouter(tags=["subgraph"])

NeighborFidelityParam = Literal["full", "depth_aware", "edges_only"]
WalkDirectionParam = Literal["outbound", "inbound", "both"]


@router.get("/subgraph/render", response_model=None)
def render_subgraph_route(
    root: str = Query("", description="Root entity_id (type:slug)"),
    hops: int = Query(1, description="BFS hops, 1-3 inclusive"),
    top_k_assertions: int = Query(7, description="Assertions per card, 1-50"),
    include_superseded: bool = Query(
        False, description="Include superseded assertions in cards"
    ),
    edge_types: list[str] | None = Query(
        default=None,
        description="Filter to these relationship types (repeated query param)",
    ),
    neighbor_fidelity: NeighborFidelityParam = Query(
        "depth_aware",
        description="Neighbor detail: full | depth_aware | edges_only",
    ),
    hub_rel_threshold: int | None = Query(
        default=None,
        description="Promote hub neighbors when relationship_count >= threshold",
    ),
) -> dict[str, Any]:
    """Render the subgraph; pass results through as a structured envelope."""
    conn = cortex_conn()
    try:
        try:
            res = render_subgraph(
                conn,
                root,
                hops,
                top_k_assertions,
                include_superseded,
                edge_types,
                neighbor_fidelity=neighbor_fidelity,
                hub_rel_threshold=hub_rel_threshold,
            )
        except SubgraphRenderError as exc:
            return JSONResponse(
                status_code=exc.status,
                content={
                    "code": exc.code,
                    "message": exc.message,
                    "source": exc.source,
                    "retryable": exc.retryable,
                    "data": exc.data,
                },
            )
        return _serialize_render_result(res)
    finally:
        conn.close()


@router.get("/subgraph/walk", response_model=None)
def walk_subgraph_route(
    root: str = Query("", description="Root entity_id (type:slug)"),
    hops: int = Query(1, description="BFS hops, 1-3 inclusive"),
    edge_types: list[str] | None = Query(
        default=None,
        description="Filter to these relationship types (repeated query param)",
    ),
    direction: WalkDirectionParam = Query(
        "both", description="Traversal direction: outbound | inbound | both"
    ),
    entity_cap: int = Query(200, description="Max entities to visit"),
    include_counts: bool = Query(True, description="Include assertion/rel counts"),
    promote_hubs: bool = Query(True, description="Add summary_row for hub nodes"),
    hub_rel_threshold: int = Query(
        20, description="Hub promotion when relationship_count >= threshold"
    ),
) -> dict[str, Any]:
    """Walk the subgraph — lean topology without assertion canvas."""
    conn = cortex_conn()
    try:
        try:
            res = walk_subgraph(
                conn,
                root,
                hops,
                edge_types,
                direction=direction,
                entity_cap=entity_cap,
                include_counts=include_counts,
                promote_hubs=promote_hubs,
                hub_rel_threshold=hub_rel_threshold,
            )
        except SubgraphWalkError as exc:
            return JSONResponse(
                status_code=exc.status,
                content={
                    "code": exc.code,
                    "message": exc.message,
                    "source": exc.source,
                    "retryable": exc.retryable,
                    "data": exc.data,
                },
            )
        return _serialize_walk_result(res)
    finally:
        conn.close()
