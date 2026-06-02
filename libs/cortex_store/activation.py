"""Spreading activation retrieval — C3 graph intelligence.

BFS walk from seed entities over association edges across BOTH substrates
(structural ``relationships`` + reasoning ``session_edges``; cortex-spec §§8–9,
thread 1174), scoring neighbor assertions by entrenchment × exponential decay
per hop.  High-degree hub entities are optionally dampened via IDF-style penalty.

Used by ``GET /assertions/activate`` and the ``cortex(tool="activate")``
MCP dispatch op.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from .db import query
from .edge_walk import active_edges

# Association type set spanning both substrates (cortex-spec §§8–9, migration
# 041; thread 1174). `activate` is associative retrieval (relatedness spreading),
# so it walks every knowledge-association type on either substrate — making this
# module's former "all active edge types" claim genuinely true. `blocked_by` is
# EXCLUDED (uniform invariant with analyze_impact): it is transient
# workflow/scheduling state, not knowledge association; a "what is waiting on
# this?" view is an explicit opt-in, never the default spread. Reasoning-only
# types (contradicts, supersedes, caused_by, analogous_to) match no structural
# rows and vice versa (child_of, references, …); the shared set is safe for both
# halves since neither substrate validates against a type registry.
_ACTIVATE_EDGE_TYPES = (
    "relates_to",
    "related_to",
    "references",
    "child_of",
    "belongs_to",
    "archives_to",
    "depends_on",
    "requires",
    "derived_from",
    "evidence_for",
    "extends",
    "supersedes",
    "caused_by",
    "analogous_to",
    "contradicts",
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
    substrates_traversed: list[str]


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
    """BFS from seed entities over association edges (both substrates), scoring by entrenchment x decay.

    Walks outward from *seed_entities* up to *depth* hops (clamped to [1, 3])
    over ``_ACTIVATE_EDGE_TYPES`` across the structural ``relationships`` and
    reasoning ``session_edges`` tables (shared :func:`edge_walk.active_edges`,
    ``direction="both"``).  At each neighbor entity, loads top active assertions
    ordered by ``entrenchment_score DESC``, then scores them:

        activation_score = entrenchment_score x decay_factor^hop x hub_penalty

    Hub penalty (when *suppress_hubs* is True): entities with edge degree
    exceeding *hub_threshold_pct* of total active edges get their contribution
    dampened by ``1 / log(1 + degree)``.  Degree and the total-edge denominator
    both span both substrates so the IDF threshold is coherent with the walk.
    """
    depth = max(1, min(depth, 3))
    exclude_set = set(exclude_ids) if exclude_ids else set()

    total_active_edges = _total_active_edge_count(conn) if suppress_hubs else 0
    hub_degree_threshold = (
        int(total_active_edges * hub_threshold_pct) if total_active_edges > 0 else 0
    )

    visited: set[str] = set(seed_entities)
    frontier: list[tuple[str, int, list[str], list[str], list[str]]] = [
        (eid, 0, [eid], [], []) for eid in seed_entities
    ]
    candidates: list[ActivatedAssertion] = []

    for _hop_round in range(depth):
        next_frontier: list[tuple[str, int, list[str], list[str], list[str]]] = []

        for entity_id, hop, path, etypes, substrates in frontier:
            for edge in active_edges(
                conn, entity_id, types=_ACTIVATE_EDGE_TYPES, direction="both"
            ):
                if edge.neighbor in visited:
                    continue
                visited.add(edge.neighbor)
                next_frontier.append(
                    (
                        edge.neighbor,
                        hop + 1,
                        [*path, edge.neighbor],
                        [*etypes, edge.edge_type],
                        [*substrates, edge.substrate],
                    )
                )

        for entity_id, hop, path, etypes, substrates in next_frontier:
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
                        substrates_traversed=substrates,
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
    """Active edge degree of *entity_id* across BOTH substrates (D2).

    No type filter — hub-ness is a property of global connectivity, measured
    over the same edge population the walk now spans (per-substrate active
    predicates).
    """
    rows = query(
        conn,
        "SELECT "
        "(SELECT COUNT(*) FROM session_edges "
        "  WHERE (from_node = ? OR to_node = ?) AND valid_until IS NULL) + "
        "(SELECT COUNT(*) FROM relationships "
        "  WHERE (from_entity = ? OR to_entity = ?) "
        "  AND active = 1 AND valid_until IS NULL) AS cnt",
        (entity_id, entity_id, entity_id, entity_id),
    )
    return rows[0]["cnt"] if rows else 0


def _total_active_edge_count(conn: sqlite3.Connection) -> int:
    """Total active edges across BOTH substrates — the hub-threshold denominator (D2)."""
    rows = query(
        conn,
        "SELECT "
        "(SELECT COUNT(*) FROM session_edges WHERE valid_until IS NULL) + "
        "(SELECT COUNT(*) FROM relationships "
        "  WHERE active = 1 AND valid_until IS NULL) AS cnt",
        (),
    )
    return rows[0]["cnt"] if rows else 0
