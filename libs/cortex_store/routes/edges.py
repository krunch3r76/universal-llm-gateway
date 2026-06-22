"""REST endpoints for querying and managing session-edge reasoning links.

This module exposes CRUD and traversal routes used by agents to seed and
inspect reasoning connections between Cortex entities across sessions.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status

from ..db import cortex_conn, query
from ..models import EdgeCreate, EdgeItem, EdgeList, EdgeRetire, EdgeUpdate

router = APIRouter(prefix="/edges", tags=["edges"])

_EDGE_COLS = (
    "id, session_id, agent, from_node, to_node, edge_type, strength, "
    "edge_source, context, prompt, seeded_by, valid_until, metadata, created_at"
)
_EDGE_SELECT_E = (
    "e.id, e.session_id, e.agent, e.from_node, e.to_node, e.edge_type, e.strength, "
    "e.edge_source, e.context, e.prompt, e.seeded_by, e.valid_until, e.metadata, e.created_at"
)

_ASSERTION_PREFIX = "assertion:"


def _endpoint_resolves(conn: sqlite3.Connection, node: str) -> bool:
    """Return True if a node address resolves to a real graph node.

    Per v2.3 §3.4, edge endpoints are addressed through one of two tables:

    - ``assertion:N`` (integer N) → looked up in the ``assertions`` table
      (assertion-as-evidence-for-entity, assertion-to-assertion provenance).
    - any other id, including prefix shapes like ``todo:``, ``agent_skill:``,
      ``doc:``, ``pattern:``, ``event:``, ``service:``, … → looked up in the
      ``entities`` table. The colon in those ids is a primary-key convention,
      not a separate namespace.

    Malformed ``assertion:`` addresses (non-integer or empty suffix) return
    False so the caller gets a clean ``dangling_edge`` error rather than an
    integer-cast exception.
    """
    if node.startswith(_ASSERTION_PREFIX):
        suffix = node[len(_ASSERTION_PREFIX) :]
        try:
            assertion_id = int(suffix)
        except ValueError:
            return False
        return bool(
            query(conn, "SELECT 1 FROM assertions WHERE id = ?", (assertion_id,))
        )
    return bool(query(conn, "SELECT 1 FROM entities WHERE id = ?", (node,)))


@router.post("", response_model=EdgeItem, status_code=status.HTTP_201_CREATED)
def create_edge(body: EdgeCreate) -> EdgeItem:
    """Create a new active session edge after validating edge_type and endpoints.

    Both ``from_node`` and ``to_node`` MUST resolve per v2.3 §3.4: either an
    ``entity_id`` (any prefix, looked up in the ``entities`` table) or
    ``assertion:N`` (looked up in the ``assertions`` table). Unresolved
    endpoints are rejected with HTTP 422 and ``reason: "dangling_edge"``.

    Distinct from the referential check in ``POST /relationships``, which
    restricts to entities only — relationships are structural; edges are
    reasoning links and may target specific historical assertions.
    """
    conn = cortex_conn()
    if not query(
        conn, "SELECT 1 FROM session_edge_types WHERE type = ?", (body.edge_type,)
    ):
        valid = [
            r["type"]
            for r in query(
                conn, "SELECT type FROM session_edge_types ORDER BY type", ()
            )
        ]
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown edge_type: {body.edge_type!r}",
                "valid_types": valid,
                "hint": "Use one of valid_types above, or GET /edges/types for descriptions and directionality.",
            },
        )
    missing = [
        node
        for node in (body.from_node, body.to_node)
        if not _endpoint_resolves(conn, node)
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "dangling_edge",
                "error": f"Edge endpoints not found: {missing}",
                "missing_nodes": missing,
                "hint": "Create the entity first (entity_create) before seeding an edge against it.",
            },
        )
    ins = (
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type, strength, "
        "edge_source, context, prompt, seeded_by, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    )
    cur = conn.execute(
        ins,
        (
            body.session_id,
            body.agent,
            body.from_node,
            body.to_node,
            body.edge_type,
            body.strength,
            body.edge_source,
            body.context,
            body.prompt,
            body.seeded_by,
            body.metadata,
        ),
    )
    conn.commit()
    row = query(
        conn, f"SELECT {_EDGE_COLS} FROM session_edges WHERE id = ?", (cur.lastrowid,)
    )
    return EdgeItem(**row[0])


@router.get("", response_model=EdgeList)
def list_edges(
    from_node: str | None = None,
    to_node: str | None = None,
    edge_type: str | None = None,
    edge_type_exclude: str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    since_hours: int | None = Query(None, ge=1, le=720),
    include_retired: bool = False,
    limit: int = Query(50, ge=1, le=500),
) -> EdgeList:
    """List edges with optional filters and active-only default behavior.

    Args:
        edge_type_exclude: Comma-separated edge types to exclude.
        since_hours: Only return edges created within this many hours.
    """
    conn = cortex_conn()
    clauses: list[str] = []
    params: list[str | int] = []
    for col, val in (
        ("from_node", from_node),
        ("to_node", to_node),
        ("edge_type", edge_type),
        ("agent", agent),
        ("session_id", session_id),
    ):
        if val:
            clauses.append(f"{col} = ?")
            params.append(val)
    if edge_type_exclude:
        excluded = [t.strip() for t in edge_type_exclude.split(",") if t.strip()]
        if excluded:
            ph = ",".join("?" for _ in excluded)
            clauses.append(f"edge_type NOT IN ({ph})")
            params.extend(excluded)
    if since_hours is not None and isinstance(since_hours, int):
        clauses.append("created_at >= datetime('now', ? || ' hours')")
        params.append(f"-{since_hours}")
    if not include_retired:
        clauses.append("valid_until IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_EDGE_COLS} FROM session_edges {where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = query(conn, sql, tuple(params))
    return EdgeList(items=[EdgeItem(**r) for r in rows], count=len(rows))


@router.get("/traverse", response_model=EdgeList)
def traverse(
    node: str,
    hops: int = Query(1, ge=1, le=10),
    edge_type: str | None = None,
    min_strength: float = Query(0.0, ge=0.0, le=1.0),
) -> EdgeList:
    """Traverse up to 10 hops from a node with type and strength filters."""
    conn = cortex_conn()
    tc = " AND e.edge_type = ?" if edge_type else ""
    p1: list[str | float] = [min_strength, node, node]
    if edge_type:
        p1.append(edge_type)
    j = "session_edges e JOIN session_edge_types t ON e.edge_type = t.type"
    w = (
        "e.valid_until IS NULL AND e.strength >= ? AND "
        "(e.from_node = ? OR (e.to_node = ? AND NOT t.directional))"
    )
    q1 = f"SELECT {_EDGE_SELECT_E} FROM {j} WHERE {w}{tc} ORDER BY e.strength DESC, e.created_at DESC"
    rows = query(conn, q1, tuple(p1))
    if hops < 2 or not rows:
        return EdgeList(items=[EdgeItem(**r) for r in rows], count=len(rows))
    visited: set[str] = {node}
    frontier: set[str] = set()
    for r in rows:
        fn, tn = r["from_node"], r["to_node"]
        frontier.add(tn if fn == node else fn)
    frontier.discard(node)
    for _ in range(hops - 1):
        if not frontier:
            break
        visited |= frontier
        ph = ",".join("?" for _ in frontier)
        excl = visited | {node}
        excl_ph = ",".join("?" for _ in excl)
        p2: list[str | float] = [min_strength, *frontier, *frontier, *excl, *excl]
        if edge_type:
            p2.append(edge_type)
        w2 = (
            f"e.valid_until IS NULL AND e.strength >= ? AND "
            f"(e.from_node IN ({ph}) OR (e.to_node IN ({ph}) AND NOT t.directional)) AND "
            f"e.from_node NOT IN ({excl_ph}) AND e.to_node NOT IN ({excl_ph}){tc}"
        )
        q2 = f"SELECT {_EDGE_SELECT_E} FROM {j} WHERE {w2} ORDER BY e.strength DESC, e.created_at DESC"
        hop_rows = query(conn, q2, tuple(p2))
        if not hop_rows:
            break
        rows.extend(hop_rows)
        next_frontier: set[str] = set()
        for r in hop_rows:
            fn, tn = r["from_node"], r["to_node"]
            for n in (fn, tn):
                if n not in visited and n != node:
                    next_frontier.add(n)
        frontier = next_frontier
    return EdgeList(items=[EdgeItem(**r) for r in rows], count=len(rows))


@router.patch("/{edge_id}/retire", response_model=EdgeItem)
def retire_edge(
    edge_id: int,
    body: EdgeRetire | None = Body(default=None),
) -> EdgeItem:
    """Retire an active edge by setting valid_until to now or caller value."""
    conn = cortex_conn()
    vu = (
        body.valid_until if body and body.valid_until else datetime.now(UTC).isoformat()
    )
    conn.execute(
        "UPDATE session_edges SET valid_until = ? WHERE id = ? AND valid_until IS NULL",
        (vu, edge_id),
    )
    conn.commit()
    rows = query(
        conn, f"SELECT {_EDGE_COLS} FROM session_edges WHERE id = ?", (edge_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Edge {edge_id} not found")
    return EdgeItem(**rows[0])


@router.patch("/{edge_id}", response_model=EdgeItem)
def update_edge(edge_id: int, body: EdgeUpdate = Body(...)) -> EdgeItem:
    """Patch mutable scalar fields of an ACTIVE session-edge in place.

    Mirrors relationship_update (non-null-only), narrowed to {strength, context,
    prompt, metadata}. Provenance (session_id/agent/seeded_by) and valid_until are
    NOT patchable. Active-only: WHERE valid_until IS NULL → a patch on a retired
    edge is a silent no-op returning 200 unchanged. 404 only on a missing id.
    """
    conn = cortex_conn()
    updates = {
        col: val
        for col, val in (
            ("strength", body.strength),
            ("context", body.context),
            ("prompt", body.prompt),
            ("metadata", body.metadata),
        )
        if val is not None
    }
    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")
    set_clause = ", ".join(f"{col} = ?" for col in updates)
    conn.execute(
        f"UPDATE session_edges SET {set_clause} WHERE id = ? AND valid_until IS NULL",
        (*updates.values(), edge_id),
    )
    conn.commit()
    rows = query(conn, f"SELECT {_EDGE_COLS} FROM session_edges WHERE id = ?", (edge_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Edge {edge_id} not found")
    return EdgeItem(**rows[0])


@router.get("/types")
def list_edge_types() -> list[dict[str, Any]]:
    """Return the registered session edge taxonomy and directionality flags."""
    return query(
        cortex_conn(),
        "SELECT type, description, directional FROM session_edge_types ORDER BY type",
    )


def _create_edge_impl(payload: dict[str, Any]) -> dict[str, Any]:
    return create_edge(EdgeCreate.model_validate(payload)).model_dump(mode="json")


def _list_edges_impl(**kwargs: object) -> dict[str, Any]:
    return list_edges(**kwargs).model_dump(mode="json")


def _traverse_edges_impl(**kwargs: object) -> dict[str, Any]:
    return traverse(**kwargs).model_dump(mode="json")


def _retire_edge_impl(edge_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    body = EdgeRetire.model_validate(payload) if payload else None
    return retire_edge(edge_id, body).model_dump(mode="json")


def _update_edge_impl(edge_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    return update_edge(edge_id, EdgeUpdate.model_validate(payload)).model_dump(mode="json")


def _list_edge_types_impl() -> list[dict[str, Any]]:
    return list_edge_types()
