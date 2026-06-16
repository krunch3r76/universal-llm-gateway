"""BFS traversal + induced edge set for subgraph rendering.

Internal helper module for :mod:`subgraph_renderer`. Not a public API \u2014
shape may change. The renderer owns validation and the entrypoint; this
module owns graph traversal mechanics.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from typing import Any, Literal

from .db import query

DirectionFromRoot = Literal["outbound", "inbound", "cross"]
WalkDirection = Literal["outbound", "inbound", "both"]

_ENTITY_CAP_DEFAULT = 50


class _CapExceededError(Exception):
    """Raised by :func:`bfs_traverse` when the entity cap fires.

    Caught by the renderer entrypoint and re-raised as a structured
    SubgraphRenderError so the renderer keeps a single error surface.
    """


def archived_set(conn: sqlite3.Connection) -> set[str]:
    """Entities that are the source of an active ``archives_to`` edge.

    Per cortex convention, ``from_entity archives_to to_entity`` means the
    from-entity is being archived into the bucket; only the from-entity is
    excluded from traversal. Batched once \u2014 replaces the N+1 per-neighbor
    lookup the grok stub used.
    """
    rows = query(
        conn,
        "SELECT DISTINCT from_entity FROM relationships "
        "WHERE type = 'archives_to' AND active = 1 AND valid_until IS NULL",
    )
    return {str(row["from_entity"]) for row in rows}


def bfs_traverse(
    *,
    conn: sqlite3.Connection,
    root: str,
    hops: int,
    edge_types: list[str] | None,
    archived: set[str],
    entity_cap: int = _ENTITY_CAP_DEFAULT,
    direction: WalkDirection = "both",
) -> dict[str, int]:
    """BFS with strict ``hops`` depth bound and entity cap.

    Returns ``{entity_id: hop_distance}`` for every visited entity
    including root. Raises :class:`_CapExceededError` if the visited set would
    exceed ``entity_cap``.

    Filters: ``valid_until IS NULL`` (canonical active-row predicate
    across cortex-api), self-loops, archived entities, optional
    ``direction`` (outbound/inbound/both), and any edge type not in
    ``edge_types`` when that filter is set.
    """
    allowed = set(edge_types) if edge_types else None
    visited: dict[str, int] = {root: 0}
    q: deque[tuple[str, int]] = deque([(root, 0)])

    while q:
        curr, d = q.popleft()
        if d >= hops:
            continue
        edge_rows = query(
            conn,
            "SELECT from_entity, to_entity, type FROM relationships "
            "WHERE (from_entity = ? OR to_entity = ?) "
            "AND active = 1 AND valid_until IS NULL",
            (curr, curr),
        )
        for row in edge_rows:
            src = str(row["from_entity"])
            tgt = str(row["to_entity"])
            typ = str(row["type"])
            if src == tgt:
                continue
            if allowed is not None and typ not in allowed:
                continue
            if direction == "outbound" and src != curr:
                continue
            if direction == "inbound" and tgt != curr:
                continue
            neigh = tgt if src == curr else src
            if neigh in visited or neigh in archived:
                continue
            if len(visited) >= entity_cap:
                raise _CapExceededError()
            visited[neigh] = d + 1
            q.append((neigh, d + 1))
    return visited


def induced_edges(
    *,
    conn: sqlite3.Connection,
    visited: dict[str, int],
    root: str,
    edge_types: list[str] | None,
) -> list[dict[str, Any]]:
    """Full induced edge set on the ``visited`` node set.

    Per Kaywan direction 2026-05-20 (deviation from V1.1 spec \u00a7"BFS
    semantics" which specified tree-edges only): captures sibling,
    cross, and back-edges so an LLM consumer sees the real local
    structure. Edge identity is ``(source, target, type, role)`` \u2014 dups
    collapse.

    Returns a list of edge dicts with keys: ``source_id``, ``target_id``,
    ``type_id``, ``role``, ``strength``, ``direction_from_root``,
    ``hop_at``. The renderer wraps these in :class:`RenderedEdge`.
    """
    visited_ids = sorted(visited)
    if not visited_ids:
        return []
    allowed = set(edge_types) if edge_types else None
    if allowed is not None and not allowed:
        return []
    vph = ",".join("?" for _ in visited_ids)
    type_clause = ""
    type_params: list[Any] = []
    if allowed is not None:
        tph = ",".join("?" for _ in allowed)
        type_clause = f" AND type IN ({tph})"
        type_params = sorted(allowed)
    sql = (
        "SELECT from_entity, to_entity, type, role, strength FROM relationships "
        "WHERE active = 1 AND valid_until IS NULL "
        f"AND from_entity IN ({vph}) AND to_entity IN ({vph}){type_clause}"
    )
    rows = query(conn, sql, tuple(visited_ids + visited_ids + type_params))
    seen: set[tuple[str, str, str, str | None]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        src = str(row["from_entity"])
        tgt = str(row["to_entity"])
        typ = str(row["type"])
        if src == tgt:
            continue
        role = row["role"]
        ident = (src, tgt, typ, role)
        if ident in seen:
            continue
        seen.add(ident)
        strength = float(row["strength"]) if row["strength"] is not None else 1.0
        out.append(
            {
                "source_id": src,
                "target_id": tgt,
                "type_id": typ,
                "role": role,
                "strength": strength,
                "direction_from_root": _direction_from_root(src, tgt, root, visited),
                "hop_at": max(visited[src], visited[tgt]),
            }
        )
    return out


def _direction_from_root(
    src: str, tgt: str, root: str, visited: dict[str, int]
) -> DirectionFromRoot:
    """Compute direction relative to root using hop_distance comparison.

    Matches V1.1 spec: outbound = root is source or path flows out;
    inbound = root is target or path flows in; cross = both endpoints
    sit at the same hop level (sibling).
    """
    if src == root:
        return "outbound"
    if tgt == root:
        return "inbound"
    if visited[src] < visited[tgt]:
        return "outbound"
    if visited[src] > visited[tgt]:
        return "inbound"
    return "cross"
