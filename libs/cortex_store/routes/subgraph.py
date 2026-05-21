"""Subgraph render route — thin FastAPI wrapper over shared renderer.

Unprefixed ``/subgraph/render`` per cortex-api convention (internal-only;
external mapping to ``/api/v1/*`` is a Stargate-side concern, separate PR).

Validation lives entirely in :func:`subgraph_renderer.render_subgraph` —
the route does NOT pre-validate. This keeps the ``.called`` / ``.failed``
event pair complete for every request, including ill-formed ones.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ..db import cortex_conn
from ..subgraph_renderer import SubgraphRenderError, render_subgraph

router = APIRouter(tags=["subgraph"])


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
) -> dict[str, Any] | JSONResponse:
    """Render the subgraph; pass results through as a structured envelope."""
    conn = cortex_conn()
    try:
        try:
            res = render_subgraph(
                conn, root, hops, top_k_assertions, include_superseded, edge_types
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
        return _serialize_result(res)
    finally:
        conn.close()


def _serialize_result(res: Any) -> dict[str, Any]:
    return {
        "rendered": res.rendered,
        "root_entity_id": res.root_entity_id,
        "entities": [
            {
                "entity_id": e.entity_id,
                "hop_distance": e.hop_distance,
                "card": e.card,
            }
            for e in res.entities
        ],
        "edges": [
            {
                "source_id": ed.source_id,
                "target_id": ed.target_id,
                "type_id": ed.type_id,
                "role": ed.role,
                "strength": ed.strength,
                "direction_from_root": ed.direction_from_root,
                "hop_at": ed.hop_at,
            }
            for ed in res.edges
        ],
        "generated_at": res.generated_at,
        "hops": res.hops,
        "entity_count": res.entity_count,
        "edge_count": res.edge_count,
    }
