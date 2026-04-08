"""Spreading activation retrieval — C3 graph intelligence.

BFS walk from seed entities over all active edge types, scoring neighbor
assertions by entrenchment × exponential decay per hop.  High-degree hub
entities are optionally dampened via IDF-style penalty.

Used by ``GET /assertions/activate`` and the ``cortex(tool="activate")``
MCP dispatch op.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from .db import query

_ALL_ACTIVE_EDGE_TYPES = (
    "relates_to",
    "contradicts",
    "evidence_for",
    "extends",
    "depends_on",
    "supersedes",
    "caused_by",
    "analogous_to",
)


@dataclass
class ActivatedAssertion:
    assertion_id: int
    entity_id: str
    claim: str
    confidence: str
    entrenchment_score: float
    activation_score: float
    hop_distance: int
    activation_path: list[str]
    edge_types_traversed: list[str]


@dataclass
class ActivationResult:
    seed_entities: list[str]
    depth: int
    activated: list[ActivatedAssertion]
    hub_suppression: bool


def spreading_activation(
    conn: sqlite3.Connection,
    seed_entities: list[str],
    *,
    depth: int = 1,
    max_results: int = 20,
    exclude_ids: list[int] | None = None,
    decay_factor: float = 0.5,
    suppress_hubs: bool = True,
    hub_threshold_pct: float = 0.30,
) -> ActivationResult:
    """BFS from seed entities over all active edge types, scoring by entrenchment x decay.

    Walks outward from *seed_entities* up to *depth* hops (clamped to [1, 3]).
    At each neighbor entity, loads top active assertions ordered by
    ``entrenchment_score DESC``, then scores them:

        activation_score = entrenchment_score x decay_factor^hop x hub_penalty

    Hub penalty (when *suppress_hubs* is True): entities with edge degree
    exceeding *hub_threshold_pct* of total active edges get their contribution
    dampened by ``1 / log(1 + degree)``.
    """
    depth = max(1, min(depth, 3))
    exclude_set = set(exclude_ids) if exclude_ids else set()

    edge_type_ph = ",".join("?" for _ in _ALL_ACTIVE_EDGE_TYPES)

    total_active_edges = _total_active_edge_count(conn) if suppress_hubs else 0
    hub_degree_threshold = (
        int(total_active_edges * hub_threshold_pct) if total_active_edges > 0 else 0
    )

    visited: set[str] = set(seed_entities)
    frontier: list[tuple[str, int, list[str], list[str]]] = [
        (eid, 0, [eid], []) for eid in seed_entities
    ]
    candidates: list[ActivatedAssertion] = []

    for _hop_round in range(depth):
        next_frontier: list[tuple[str, int, list[str], list[str]]] = []

        for entity_id, hop, path, etypes in frontier:
            neighbors = _neighbors_both_directions(conn, entity_id, edge_type_ph)
            for neighbor_id, edge_type in neighbors:
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                next_frontier.append(
                    (neighbor_id, hop + 1, [*path, neighbor_id], [*etypes, edge_type])
                )

        for entity_id, hop, path, etypes in next_frontier:
            hub_penalty = 1.0
            if suppress_hubs and hub_degree_threshold > 0:
                degree = _entity_edge_degree(conn, entity_id)
                if degree > hub_degree_threshold:
                    hub_penalty = 1.0 / math.log(1.0 + degree)

            assertions = _top_assertions_by_entrenchment(conn, entity_id, limit=10)
            for a in assertions:
                if a["id"] in exclude_set:
                    continue
                entrenchment = a.get("entrenchment_score") or 0.0
                score = entrenchment * (decay_factor**hop) * hub_penalty
                candidates.append(
                    ActivatedAssertion(
                        assertion_id=a["id"],
                        entity_id=entity_id,
                        claim=a["claim"],
                        confidence=a["confidence"],
                        entrenchment_score=entrenchment,
                        activation_score=round(score, 6),
                        hop_distance=hop,
                        activation_path=path,
                        edge_types_traversed=etypes,
                    )
                )

        frontier = next_frontier

    candidates.sort(key=lambda c: c.activation_score, reverse=True)
    return ActivationResult(
        seed_entities=seed_entities,
        depth=depth,
        activated=candidates[:max_results],
        hub_suppression=suppress_hubs,
    )


def _neighbors_both_directions(
    conn: sqlite3.Connection,
    entity_id: str,
    edge_type_ph: str,
) -> list[tuple[str, str]]:
    """Return (neighbor_id, edge_type) for all active edges touching entity_id."""
    rows = query(
        conn,
        "SELECT from_node, to_node, edge_type FROM session_edges "
        f"WHERE (from_node = ? OR to_node = ?) AND edge_type IN ({edge_type_ph}) "
        "AND valid_until IS NULL",
        (entity_id, entity_id, *_ALL_ACTIVE_EDGE_TYPES),
    )
    results: list[tuple[str, str]] = []
    for row in rows:
        neighbor = row["to_node"] if row["from_node"] == entity_id else row["from_node"]
        results.append((neighbor, row["edge_type"]))
    return results


def _top_assertions_by_entrenchment(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    limit: int = 10,
) -> list[dict[str, object]]:
    return query(
        conn,
        "SELECT id, entity_id, claim, confidence, entrenchment_score "
        "FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL "
        "ORDER BY entrenchment_score DESC NULLS LAST "
        "LIMIT ?",
        (entity_id, limit),
    )


def _entity_edge_degree(conn: sqlite3.Connection, entity_id: str) -> int:
    rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM session_edges "
        "WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL",
        (entity_id, entity_id),
    )
    return rows[0]["cnt"] if rows else 0


def _total_active_edge_count(conn: sqlite3.Connection) -> int:
    rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM session_edges WHERE valid_until IS NULL",
        (),
    )
    return rows[0]["cnt"] if rows else 0
