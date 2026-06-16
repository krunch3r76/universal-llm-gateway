"""Dispatch ops for ``render_subgraph`` and ``walk_subgraph``.

Thin wrappers with parity to REST routes. Returns ``{"error": "..."}`` on
failure to match sibling-ops convention. Validation lives in the renderer
and walker modules.
"""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..subgraph_renderer import SubgraphRenderError, render_subgraph
from ..subgraph_walker import SubgraphWalkError, walk_subgraph


def _op_render_subgraph(
    root: str | None = None,
    hops: int = 1,
    top_k_assertions: int = 7,
    include_superseded: bool = False,
    edge_types: list[str] | None = None,
    neighbor_fidelity: str = "depth_aware",
    hub_rel_threshold: int | None = None,
    **_: object,
) -> dict[str, Any]:
    """Render a subgraph via the shared renderer."""
    conn = cortex_conn()
    try:
        try:
            res = render_subgraph(
                conn,
                root or "",
                hops,
                top_k_assertions,
                include_superseded,
                edge_types,
                neighbor_fidelity=neighbor_fidelity,  # type: ignore[arg-type]
                hub_rel_threshold=hub_rel_threshold,
            )
        except SubgraphRenderError as exc:
            return {
                "error": exc.message,
                "code": exc.code,
                "source": exc.source,
                "retryable": exc.retryable,
                "data": exc.data,
            }
        return _serialize_render_result(res)
    finally:
        conn.close()


def _op_walk_subgraph(
    root: str | None = None,
    hops: int = 1,
    edge_types: list[str] | None = None,
    direction: str = "both",
    entity_cap: int = 200,
    include_counts: bool = True,
    promote_hubs: bool = True,
    hub_rel_threshold: int = 20,
    **_: object,
) -> dict[str, Any]:
    """Walk a subgraph — lean topology without assertion canvas."""
    conn = cortex_conn()
    try:
        try:
            res = walk_subgraph(
                conn,
                root or "",
                hops,
                edge_types,
                direction=direction,  # type: ignore[arg-type]
                entity_cap=entity_cap,
                include_counts=include_counts,
                promote_hubs=promote_hubs,
                hub_rel_threshold=hub_rel_threshold,
            )
        except SubgraphWalkError as exc:
            return {
                "error": exc.message,
                "code": exc.code,
                "source": exc.source,
                "retryable": exc.retryable,
                "data": exc.data,
            }
        return _serialize_walk_result(res)
    finally:
        conn.close()


def _serialize_render_result(res: Any) -> dict[str, Any]:
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
        "neighbor_fidelity": res.neighbor_fidelity,
    }


def _serialize_walk_result(res: Any) -> dict[str, Any]:
    return {
        "root_entity_id": res.root_entity_id,
        "nodes": [
            {
                "entity_id": n.entity_id,
                "type": n.entity_type,
                "name": n.name,
                "hop_distance": n.hop_distance,
                "active_assertion_count": n.active_assertion_count,
                "relationship_count": n.relationship_count,
                "edge_types": n.edge_types,
                "summary_row": n.summary_row,
                "predicate_summary": n.predicate_summary,
                "status_summary": n.status_summary,
            }
            for n in res.nodes
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
        "rendered_table": res.rendered_table,
        "generated_at": res.generated_at,
        "hops": res.hops,
        "entity_count": res.entity_count,
        "edge_count": res.edge_count,
    }
