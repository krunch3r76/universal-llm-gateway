"""Dispatch op for ``render_subgraph`` — thin wrapper, parity with REST route.

Returns ``{"error": "..."}`` on failure to match sibling-ops convention
(see ``ops_entities.py`` et al.) — the friction-hint path in
``dispatch_ops/__init__.py`` keys off the ``"error"`` field. Structured
ProtocolError fields (``code``, ``data``) are preserved alongside for
clients that want them.

Validation lives entirely in :func:`subgraph_renderer.render_subgraph`.
"""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..subgraph_renderer import SubgraphRenderError, render_subgraph


def _op_render_subgraph(
    root: str | None = None,
    hops: int = 1,
    top_k_assertions: int = 7,
    include_superseded: bool = False,
    edge_types: list[str] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Render a subgraph via the shared renderer.

    Success: returns the structured envelope. Failure: returns
    ``{"error": ..., "code": ..., "data": ...}`` so the dispatch friction
    hint attaches and the caller can still introspect structured fields.
    """
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
            )
        except SubgraphRenderError as exc:
            return {
                "error": exc.message,
                "code": exc.code,
                "source": exc.source,
                "retryable": exc.retryable,
                "data": exc.data,
            }
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
