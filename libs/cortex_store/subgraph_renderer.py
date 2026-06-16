"""Render a cortex subgraph as deterministic markdown + structured envelope.

Single source of validation truth for the subgraph render surface. The
REST route (``routes/subgraph.py``) and the dispatch op
(``dispatch_ops/ops_subgraph.py``) both call :func:`render_subgraph`
directly; they only convert error envelope shape on the way out.

Composes :mod:`subgraph_traversal`, :mod:`subgraph_cards`, and
:mod:`subgraph_template`. Emits
``cortex.subgraph.render.{called,completed,failed}`` events with a
shared ``render_id`` for correlation.

Edge response semantics (deviation from V1.1 spec, per Kaywan direction
2026-05-20): full induced subgraph on visited nodes, not BFS tree edges.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from universal_logging import get_logger

from .db import query
from .event_publisher import (
    cortex_subgraph_render_called,
    cortex_subgraph_render_completed,
    cortex_subgraph_render_failed,
)
from .subgraph_cards import (
    CardBuildError,
    _batch_relationship_counts,
    augment_entity_columns,
    build_cards,
)
from .subgraph_neighbor_fidelity import (
    NeighborFidelity,
    hub_rel_threshold_default,
)
from .subgraph_template import build_subgraph_markdown
from .subgraph_traversal import (
    _CapExceededError,
    archived_set,
    bfs_traverse,
    induced_edges,
)
from .subgraph_walker import walk_subgraph

logger = get_logger(__name__)

DirectionFromRoot = Literal["outbound", "inbound", "cross"]

_ENTITY_CAP = 50
_HOPS_MIN = 1
_HOPS_MAX = 3
_TOP_K_MIN = 1
_TOP_K_MAX = 50


@dataclass
class RenderedEdge:
    source_id: str
    target_id: str
    type_id: str
    role: str | None
    strength: float
    direction_from_root: DirectionFromRoot
    hop_at: int


@dataclass
class RenderedEntity:
    entity_id: str
    hop_distance: int
    card: dict[str, Any]


@dataclass
class SubgraphRenderResult:
    rendered: str
    root_entity_id: str
    entities: list[RenderedEntity]
    edges: list[RenderedEdge]
    generated_at: str
    hops: int
    entity_count: int
    edge_count: int
    neighbor_fidelity: NeighborFidelity = "depth_aware"


class SubgraphRenderError(Exception):
    """Structured render error carrying ProtocolError envelope fields."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 422,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status = status
        self.data = data or {}
        self.source = "cortex-api"
        self.retryable = False
        super().__init__(message)


def render_subgraph(
    conn: sqlite3.Connection,
    root: str,
    hops: int = 1,
    top_k_assertions: int = 7,
    include_superseded: bool = False,
    edge_types: list[str] | None = None,
    neighbor_fidelity: NeighborFidelity = "depth_aware",
    hub_rel_threshold: int | None = None,
) -> SubgraphRenderResult:
    """Render a cortex subgraph rooted at ``root``.

    Emits ``.called`` on entry, ``.completed`` on success, ``.failed``
    on every error path. All events carry a shared ``render_id``.
    """
    hub_threshold = (
        hub_rel_threshold
        if hub_rel_threshold is not None
        else hub_rel_threshold_default()
    )

    if neighbor_fidelity == "edges_only":
        walk = walk_subgraph(
            conn,
            root=root,
            hops=hops,
            edge_types=edge_types,
            hub_rel_threshold=hub_threshold,
        )
        return _walk_as_render_result(walk, neighbor_fidelity="edges_only")

    t0 = time.perf_counter()
    render_id = uuid.uuid4().hex
    r = (root or "").strip()

    cortex_subgraph_render_called(
        render_id=render_id,
        root=r,
        hops=hops,
        edge_types_count=len(edge_types) if edge_types else 0,
        top_k_assertions=top_k_assertions,
        include_superseded=include_superseded,
    )

    _validate_params(
        render_id=render_id,
        r=r,
        hops=hops,
        top_k_assertions=top_k_assertions,
        edge_types=edge_types,
        conn=conn,
    )

    if not query(conn, "SELECT 1 FROM entities WHERE id = ?", (r,)):
        _fail(render_id, r, "entity_not_found", hops)
        raise SubgraphRenderError(
            "entity_not_found", f"Entity not found: {r}", 404, {"entity_id": r}
        )

    try:
        visited = bfs_traverse(
            conn=conn,
            root=r,
            hops=hops,
            edge_types=edge_types,
            archived=archived_set(conn),
        )
    except _CapExceededError:
        _fail(render_id, r, "entity_cap_exceeded", hops)
        raise SubgraphRenderError(
            "subgraph_too_large",
            f"Subgraph exceeds {_ENTITY_CAP} entities",
            422,
            {
                "entity_cap": _ENTITY_CAP,
                "root": r,
                "hops": hops,
                "partial_count": _ENTITY_CAP + 1,
            },
        ) from None

    edge_dicts = induced_edges(
        conn=conn, visited=visited, root=r, edge_types=edge_types
    )
    rendered_edges = [
        RenderedEdge(
            source_id=e["source_id"],
            target_id=e["target_id"],
            type_id=e["type_id"],
            role=e["role"],
            strength=e["strength"],
            direction_from_root=e["direction_from_root"],
            hop_at=e["hop_at"],
        )
        for e in edge_dicts
    ]
    edges_sorted = sorted(rendered_edges, key=_edge_sort_key)

    rel_counts = _batch_relationship_counts(conn, list(visited))

    try:
        cards = build_cards(
            conn=conn,
            visited_ids=list(visited),
            top_k_assertions=top_k_assertions,
            include_superseded=include_superseded,
            root=r,
            visited=visited,
            neighbor_fidelity=neighbor_fidelity,
            hub_rel_threshold=hub_threshold,
            rel_counts=rel_counts,
        )
    except CardBuildError as exc:
        _fail(render_id, r, "card_build_failed", hops)
        raise SubgraphRenderError(
            "card_build_failed",
            str(exc),
            500,
            {"entity_id": exc.entity_id, "error": str(exc.original)},
        ) from exc

    descriptions, statuses = augment_entity_columns(conn, list(visited))
    entity_objs = _sort_entities(visited=visited, cards=cards, root=r)

    rendered_md = build_subgraph_markdown(
        root_id=r,
        cards=cards,
        descriptions=descriptions,
        statuses=statuses,
        entity_objs=entity_objs,
        edges=edges_sorted,
        hops=hops,
        top_k_assertions=top_k_assertions,
        neighbor_fidelity=neighbor_fidelity,
        hub_rel_threshold=hub_threshold,
        rel_counts=rel_counts,
    )

    duration_ms = int((time.perf_counter() - t0) * 1000)
    result = SubgraphRenderResult(
        rendered=rendered_md,
        root_entity_id=r,
        entities=entity_objs,
        edges=edges_sorted,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        hops=hops,
        entity_count=len(entity_objs),
        edge_count=len(edges_sorted),
        neighbor_fidelity=neighbor_fidelity,
    )
    cortex_subgraph_render_completed(
        render_id=render_id,
        root=r,
        hops=hops,
        entity_count=result.entity_count,
        edge_count=result.edge_count,
        duration_ms=duration_ms,
        rendered_bytes=len(rendered_md.encode("utf-8")),
    )
    return result


def _walk_as_render_result(walk: Any, *, neighbor_fidelity: NeighborFidelity) -> SubgraphRenderResult:
    entities = [
        RenderedEntity(
            entity_id=n.entity_id,
            hop_distance=n.hop_distance,
            card={
                "entity_id": n.entity_id,
                "type": n.entity_type,
                "name": n.name,
                "active_assertion_count": n.active_assertion_count,
                "relationship_count": n.relationship_count,
                "edge_types": n.edge_types,
                "summary_row": n.summary_row,
                "predicate_summary": n.predicate_summary,
                "status_summary": n.status_summary,
            },
        )
        for n in walk.nodes
    ]
    edges = [
        RenderedEdge(
            source_id=e.source_id,
            target_id=e.target_id,
            type_id=e.type_id,
            role=e.role,
            strength=e.strength,
            direction_from_root=e.direction_from_root,
            hop_at=e.hop_at,
        )
        for e in walk.edges
    ]
    return SubgraphRenderResult(
        rendered=walk.rendered_table,
        root_entity_id=walk.root_entity_id,
        entities=entities,
        edges=edges,
        generated_at=walk.generated_at,
        hops=walk.hops,
        entity_count=walk.entity_count,
        edge_count=walk.edge_count,
        neighbor_fidelity=neighbor_fidelity,
    )


def _fail(render_id: str, root: str, reason: str, hops: int) -> None:
    cortex_subgraph_render_failed(
        render_id=render_id, root=root, reason=reason, hops=hops
    )


def _validate_params(
    *,
    render_id: str,
    r: str,
    hops: int,
    top_k_assertions: int,
    edge_types: list[str] | None,
    conn: sqlite3.Connection,
) -> None:
    if not r:
        _fail(render_id, "", "root_missing", hops)
        raise SubgraphRenderError(
            "validation_error",
            "root is required",
            422,
            {"field": "root", "reason": "missing_or_empty"},
        )
    if not (_HOPS_MIN <= hops <= _HOPS_MAX):
        _fail(render_id, r, "hops_out_of_range", hops)
        raise SubgraphRenderError(
            "validation_error",
            f"hops must be in [{_HOPS_MIN},{_HOPS_MAX}]",
            422,
            {"field": "hops", "value": hops, "valid_range": [_HOPS_MIN, _HOPS_MAX]},
        )
    if not (_TOP_K_MIN <= top_k_assertions <= _TOP_K_MAX):
        _fail(render_id, r, "top_k_out_of_range", hops)
        raise SubgraphRenderError(
            "validation_error",
            f"top_k_assertions must be in [{_TOP_K_MIN},{_TOP_K_MAX}]",
            422,
            {
                "field": "top_k_assertions",
                "value": top_k_assertions,
                "valid_range": [_TOP_K_MIN, _TOP_K_MAX],
            },
        )
    if edge_types:
        known_rows = query(conn, "SELECT type FROM relationship_types ORDER BY type")
        known = {row["type"] for row in known_rows}
        unknown = [t for t in edge_types if t not in known]
        if unknown:
            _fail(render_id, r, "unknown_edge_type", hops)
            raise SubgraphRenderError(
                "validation_error",
                f"Unknown relationship type(s): {unknown}",
                422,
                {
                    "field": "edge_types",
                    "unknown": unknown,
                    "known_sample": sorted(known)[:5],
                },
            )


def _sort_entities(
    *, visited: dict[str, int], cards: dict[str, dict[str, Any]], root: str
) -> list[RenderedEntity]:
    def key(eid: str) -> tuple[int, int, str, str]:
        is_root = 0 if eid == root else 1
        return (is_root, visited[eid], str(cards[eid].get("type", "")), eid)

    return [
        RenderedEntity(entity_id=eid, hop_distance=visited[eid], card=cards[eid])
        for eid in sorted(visited, key=key)
    ]


def _edge_sort_key(e: RenderedEdge) -> tuple[str, str, str, str]:
    role = "" if e.role is None else e.role
    return (e.type_id, e.target_id, role, e.direction_from_root)
