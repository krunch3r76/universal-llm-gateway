"""Lean subgraph walk — edge topology without assertion canvas.

Composes :mod:`subgraph_traversal` for BFS + induced edges. Does not call
``get_entity_card`` / ``build_cards``. Emits
``cortex.subgraph.walk.{called,completed,failed}`` events.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from .card_adapters import get_adapter
from .confidence_field import confidence_field
from .db import query
from .event_publisher import (
    cortex_subgraph_walk_called,
    cortex_subgraph_walk_completed,
    cortex_subgraph_walk_failed,
)
from .predicate_summary import aggregate_predicate_summary
from .status_trait_read import card_status_summary_option_c
from .subgraph_traversal import (
    WalkDirection,
    _CapExceededError,
    archived_set,
    bfs_traverse,
    induced_edges,
)

logger = get_logger(__name__)

_HOPS_MIN = 1
_HOPS_MAX = 3
_ENTITY_CAP_DEFAULT = 200
_HUB_REL_THRESHOLD_DEFAULT = 20
_SUMMARY_ROW_MAX = 120

_DIRECTION_ARROW = {"outbound": "\u2192", "inbound": "\u2190", "cross": "\u2194"}


@dataclass
class WalkedEdge:
    source_id: str
    target_id: str
    type_id: str
    role: str | None
    strength: float
    direction_from_root: str
    hop_at: int


@dataclass
class WalkedNode:
    entity_id: str
    entity_type: str
    name: str
    hop_distance: int
    active_assertion_count: int
    relationship_count: int
    edge_types: list[str]
    summary_row: str | None = None
    predicate_summary: str | None = None
    status_summary: dict[str, Any] | None = None


@dataclass
class SubgraphWalkResult:
    nodes: list[WalkedNode]
    edges: list[WalkedEdge]
    rendered_table: str
    root_entity_id: str
    generated_at: str
    hops: int
    entity_count: int
    edge_count: int


class SubgraphWalkError(Exception):
    """Structured walk error carrying ProtocolError envelope fields."""

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


def walk_subgraph(
    conn: sqlite3.Connection,
    root: str,
    hops: int = 1,
    edge_types: list[str] | None = None,
    direction: WalkDirection = "both",
    entity_cap: int = _ENTITY_CAP_DEFAULT,
    include_counts: bool = True,
    promote_hubs: bool = True,
    hub_rel_threshold: int = _HUB_REL_THRESHOLD_DEFAULT,
) -> SubgraphWalkResult:
    """Walk the subgraph rooted at ``root`` — lean topology, no assertion canvas."""
    t0 = time.perf_counter()
    walk_id = uuid.uuid4().hex
    r = (root or "").strip()

    cortex_subgraph_walk_called(
        walk_id=walk_id,
        root=r,
        hops=hops,
        edge_types_count=len(edge_types) if edge_types else 0,
        direction=direction,
        entity_cap=entity_cap,
        include_counts=include_counts,
        promote_hubs=promote_hubs,
    )

    _validate_params(
        walk_id=walk_id,
        r=r,
        hops=hops,
        entity_cap=entity_cap,
        edge_types=edge_types,
        conn=conn,
    )

    if not query(conn, "SELECT 1 FROM entities WHERE id = ?", (r,)):
        _fail(walk_id, r, "entity_not_found", hops)
        raise SubgraphWalkError(
            "entity_not_found", f"Entity not found: {r}", 404, {"entity_id": r}
        )

    try:
        visited = bfs_traverse(
            conn=conn,
            root=r,
            hops=hops,
            edge_types=edge_types,
            archived=archived_set(conn),
            entity_cap=entity_cap,
            direction=direction,
        )
    except _CapExceededError:
        _fail(walk_id, r, "entity_cap_exceeded", hops)
        raise SubgraphWalkError(
            "subgraph_too_large",
            f"Subgraph exceeds {entity_cap} entities",
            422,
            {
                "entity_cap": entity_cap,
                "root": r,
                "hops": hops,
                "partial_count": entity_cap + 1,
            },
        ) from None

    edge_dicts = induced_edges(
        conn=conn, visited=visited, root=r, edge_types=edge_types
    )
    walked_edges = [
        WalkedEdge(
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
    edges_sorted = sorted(walked_edges, key=_edge_sort_key)

    entity_rows = _fetch_entity_rows(conn, list(visited))
    assn_counts = _batch_assertion_counts(conn, list(visited)) if include_counts else {}
    rel_counts = _batch_relationship_counts(conn, list(visited)) if include_counts else {}
    edge_labels = _edge_labels_by_node(edges_sorted, root=r)

    nodes = _build_nodes(
        visited=visited,
        root=r,
        entity_rows=entity_rows,
        assn_counts=assn_counts,
        rel_counts=rel_counts,
        edge_labels=edge_labels,
        conn=conn,
        promote_hubs=promote_hubs,
        hub_rel_threshold=hub_rel_threshold,
    )
    table = _render_markdown_table(nodes, edges_sorted)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    result = SubgraphWalkResult(
        nodes=nodes,
        edges=edges_sorted,
        rendered_table=table,
        root_entity_id=r,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        hops=hops,
        entity_count=len(nodes),
        edge_count=len(edges_sorted),
    )
    table_bytes = len(table.encode("utf-8"))
    envelope_bytes = _estimate_envelope_bytes(result)
    cortex_subgraph_walk_completed(
        walk_id=walk_id,
        root=r,
        hops=hops,
        entity_count=result.entity_count,
        edge_count=result.edge_count,
        duration_ms=duration_ms,
        envelope_bytes=envelope_bytes,
        table_bytes=table_bytes,
    )
    return result


def _fail(walk_id: str, root: str, reason: str, hops: int) -> None:
    cortex_subgraph_walk_failed(
        walk_id=walk_id, root=root, reason=reason, hops=hops
    )


def _validate_params(
    *,
    walk_id: str,
    r: str,
    hops: int,
    entity_cap: int,
    edge_types: list[str] | None,
    conn: sqlite3.Connection,
) -> None:
    if not r:
        _fail(walk_id, "", "root_missing", hops)
        raise SubgraphWalkError(
            "validation_error",
            "root is required",
            422,
            {"field": "root", "reason": "missing_or_empty"},
        )
    if not (_HOPS_MIN <= hops <= _HOPS_MAX):
        _fail(walk_id, r, "hops_out_of_range", hops)
        raise SubgraphWalkError(
            "validation_error",
            f"hops must be in [{_HOPS_MIN},{_HOPS_MAX}]",
            422,
            {"field": "hops", "value": hops, "valid_range": [_HOPS_MIN, _HOPS_MAX]},
        )
    if entity_cap < 1:
        _fail(walk_id, r, "entity_cap_invalid", hops)
        raise SubgraphWalkError(
            "validation_error",
            "entity_cap must be >= 1",
            422,
            {"field": "entity_cap", "value": entity_cap},
        )
    if edge_types:
        known_rows = query(conn, "SELECT type FROM relationship_types ORDER BY type")
        known = {row["type"] for row in known_rows}
        unknown = [t for t in edge_types if t not in known]
        if unknown:
            _fail(walk_id, r, "unknown_edge_type", hops)
            raise SubgraphWalkError(
                "validation_error",
                f"Unknown relationship type(s): {unknown}",
                422,
                {
                    "field": "edge_types",
                    "unknown": unknown,
                    "known_sample": sorted(known)[:5],
                },
            )


def _fetch_entity_rows(
    conn: sqlite3.Connection, entity_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not entity_ids:
        return {}
    ph = ",".join("?" for _ in entity_ids)
    rows = query(
        conn,
        f"SELECT id, type, name, description, workflow_state, updated_at, "
        f"lifecycle, confidence_band, adoption FROM entities WHERE id IN ({ph})",
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


def _edge_labels_by_node(
    edges: list[WalkedEdge], *, root: str
) -> dict[str, list[str]]:
    labels: dict[str, set[str]] = {}
    for edge in edges:
        for node_id in (edge.source_id, edge.target_id):
            if node_id == root:
                continue
            arrow = _DIRECTION_ARROW.get(edge.direction_from_root, "\u2192")
            label = f"{edge.type_id} {arrow}"
            labels.setdefault(node_id, set()).add(label)
    return {k: sorted(v) for k, v in labels.items()}


def _truncate_summary(text: str | None) -> str | None:
    if not text:
        return None
    s = str(text).strip()
    if len(s) <= _SUMMARY_ROW_MAX:
        return s
    return s[: _SUMMARY_ROW_MAX - 1] + "\u2026"


def _root_predicate_summary(conn: sqlite3.Connection, root: str) -> str:
    a_rows = query(
        conn,
        "SELECT claim, predicate_form, entrenchment_score, observed_at "
        "FROM assertions WHERE entity_id = ? AND superseded_by IS NULL "
        "ORDER BY COALESCE(entrenchment_score,0) DESC, "
        "COALESCE(observed_at,'') DESC, id DESC LIMIT 7",
        (root,),
    )
    et_rows = query(
        conn,
        "SELECT type AS type_id, COUNT(*) AS n FROM relationships "
        "WHERE (from_entity = ? OR to_entity = ?) AND active = 1 "
        "AND valid_until IS NULL GROUP BY type ORDER BY n DESC",
        (root, root),
    )
    arc_rows = query(
        conn,
        "SELECT to_entity FROM relationships "
        "WHERE from_entity = ? AND type = 'archives_to' AND active = 1",
        (root,),
    )
    return aggregate_predicate_summary(
        top_k_assertions=[dict(r) for r in a_rows],
        et_type_counts=[
            {"type_id": str(r["type_id"]), "count": int(r["n"])} for r in et_rows
        ],
        archives_to_children=[str(r["to_entity"]) for r in arc_rows],
        entity_id=root,
    )


def _build_nodes(
    *,
    visited: dict[str, int],
    root: str,
    entity_rows: dict[str, dict[str, Any]],
    assn_counts: dict[str, int],
    rel_counts: dict[str, int],
    edge_labels: dict[str, list[str]],
    conn: sqlite3.Connection,
    promote_hubs: bool,
    hub_rel_threshold: int,
) -> list[WalkedNode]:
    root_predicate = _root_predicate_summary(conn, root) if root in visited else ""
    root_status: dict[str, Any] | None = None
    if root in entity_rows:
        root_row = entity_rows[root]
        root_cf = confidence_field(conn, str(root_row.get("type", "")))
        root_status = card_status_summary_option_c(
            root_row,
            confidence_field=root_cf,
            extra={
                "workflow_state": root_row.get("workflow_state"),
                "updated_at": root_row.get("updated_at"),
            },
        )

    def sort_key(eid: str) -> tuple[int, int, str, str]:
        is_root = 0 if eid == root else 1
        row = entity_rows.get(eid, {})
        return (is_root, visited[eid], str(row.get("type", "")), eid)

    nodes: list[WalkedNode] = []
    for eid in sorted(visited, key=sort_key):
        row = entity_rows.get(eid, {})
        rel_n = rel_counts.get(eid, 0)
        summary: str | None = None
        if promote_hubs and rel_n >= hub_rel_threshold:
            adapter = get_adapter(str(row.get("type", "")))
            summary = _truncate_summary(adapter.summary_row(row))
        node = WalkedNode(
            entity_id=eid,
            entity_type=str(row.get("type", "")),
            name=str(row.get("name", eid)),
            hop_distance=visited[eid],
            active_assertion_count=assn_counts.get(eid, 0),
            relationship_count=rel_n,
            edge_types=edge_labels.get(eid, []),
            summary_row=summary,
            predicate_summary=root_predicate if eid == root else None,
            status_summary=root_status if eid == root else None,
        )
        nodes.append(node)
    return nodes


def _render_markdown_table(
    nodes: list[WalkedNode], edges: list[WalkedEdge]
) -> str:
    lines = [
        "| id | name | type | hop | assns | rels | edges |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for node in nodes:
        edge_col = ", ".join(node.edge_types) if node.edge_types else ""
        name = node.name if len(node.name) <= 48 else node.name[:47] + "\u2026"
        lines.append(
            f"| `{node.entity_id}` | {name} | {node.entity_type} | "
            f"{node.hop_distance} | {node.active_assertion_count} | "
            f"{node.relationship_count} | {edge_col} |"
        )
    if edges:
        lines.append("")
        lines.append(f"_{len(edges)} induced edges (see structured envelope)_")
    return "\n".join(lines) + "\n"


def _edge_sort_key(e: WalkedEdge) -> tuple[str, str, str, str]:
    role = "" if e.role is None else e.role
    return (e.type_id, e.target_id, role, e.direction_from_root)


def _estimate_envelope_bytes(result: SubgraphWalkResult) -> int:
    import json

    payload = {
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
            for n in result.nodes
        ],
        "edges": [
            {
                "source_id": e.source_id,
                "target_id": e.target_id,
                "type_id": e.type_id,
            }
            for e in result.edges
        ],
    }
    return len(json.dumps(payload).encode("utf-8"))
