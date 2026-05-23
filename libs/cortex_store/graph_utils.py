"""Graph intelligence helpers — BFS impact analysis and contradiction detection.

Shared between the /edges/impact endpoint (C1) and write-path contradiction
check in POST /assertions (C2).  All queries are scoped to the edges of the
seed entity — O(degree) per hop, never a full graph scan.

Spreading activation (C3) lives in ``activation.py``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from universal_logging import get_logger

from .db import query

logger = get_logger("cortex-api.graph")

_DEPENDENCY_EDGE_TYPES = ("depends_on", "evidence_for", "extends")

_ENTITY_ID_PATTERN = re.compile(
    r"\b([a-z][a-z_]*:[a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE
)


# ── C1: Impact Analysis ──────────────────────────────────────────────────


@dataclass
class ImpactedEntity:
    entity_id: str
    entity_name: str | None
    hop_distance: int
    path_trace: list[str]
    assertion_count: int
    edge_types: list[str]


@dataclass
class ImpactResult:
    seed_entity: str
    depth: int
    impacted_entities: list[ImpactedEntity]
    total_impacted_assertions: int


def analyze_impact(
    conn: sqlite3.Connection,
    seed_entity: str,
    depth: int = 2,
) -> ImpactResult:
    """BFS from *seed_entity* over dependency edges, collecting impacted entities.

    Follows **incoming** ``depends_on``, ``evidence_for``, and ``extends`` edges
    (active only — ``valid_until IS NULL``).  Edge convention: ``A depends_on B``
    means A's ``from_node`` → B's ``to_node``.  When B (seed) changes, A is
    impacted — so we follow edges where ``to_node = seed`` and collect
    ``from_node`` as the impacted entity.

    Depth is clamped to [1, 5].  Cycle detection via visited set.
    """
    depth = max(1, min(depth, 5))

    edge_type_ph = ",".join("?" for _ in _DEPENDENCY_EDGE_TYPES)

    visited: set[str] = {seed_entity}
    # Each entry: (entity_id, hop_distance, path from seed)
    frontier: list[tuple[str, int, list[str]]] = [(seed_entity, 0, [seed_entity])]
    impacted: list[ImpactedEntity] = []
    total_assertions = 0

    for _ in range(depth):
        next_frontier: list[tuple[str, int, list[str]]] = []

        for entity_id, hop, path in frontier:
            rows = query(
                conn,
                "SELECT from_node, edge_type FROM session_edges "
                f"WHERE to_node = ? AND edge_type IN ({edge_type_ph}) "
                "AND valid_until IS NULL",
                (entity_id, *_DEPENDENCY_EDGE_TYPES),
            )

            for row in rows:
                target = row["from_node"]
                if target in visited:
                    continue
                visited.add(target)
                new_path = [*path, target]
                next_frontier.append((target, hop + 1, new_path))

        for entity_id, hop, path in next_frontier:
            entity_name = _entity_name(conn, entity_id)
            a_count = _active_assertion_count(conn, entity_id)
            total_assertions += a_count
            edge_types_used = _path_edge_types(conn, path, edge_type_ph)

            impacted.append(
                ImpactedEntity(
                    entity_id=entity_id,
                    entity_name=entity_name,
                    hop_distance=hop,
                    path_trace=path,
                    assertion_count=a_count,
                    edge_types=sorted(set(edge_types_used)),
                )
            )

        frontier = next_frontier

    return ImpactResult(
        seed_entity=seed_entity,
        depth=depth,
        impacted_entities=impacted,
        total_impacted_assertions=total_assertions,
    )


def _entity_name(conn: sqlite3.Connection, entity_id: str) -> str | None:
    rows = query(conn, "SELECT name FROM entities WHERE id = ?", (entity_id,))
    return rows[0]["name"] if rows else None


def _active_assertion_count(conn: sqlite3.Connection, entity_id: str) -> int:
    rows = query(
        conn,
        "SELECT COUNT(*) AS cnt FROM assertions "
        "WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    return rows[0]["cnt"] if rows else 0


def _path_edge_types(
    conn: sqlite3.Connection,
    path: list[str],
    edge_type_ph: str,
) -> list[str]:
    """Collect distinct edge types along a path trace.

    Path goes seed → impacted, but edges point impacted → seed (from_node
    depends_on to_node).  So for consecutive path nodes [A, B], the edge is
    ``from_node=B, to_node=A``.
    """
    types: list[str] = []
    for i in range(len(path) - 1):
        rows = query(
            conn,
            "SELECT DISTINCT edge_type FROM session_edges "
            f"WHERE from_node = ? AND to_node = ? AND edge_type IN ({edge_type_ph}) "
            "AND valid_until IS NULL",
            (path[i + 1], path[i], *_DEPENDENCY_EDGE_TYPES),
        )
        types.extend(r["edge_type"] for r in rows)
    return types


# ── C2: Write-Path Contradiction Check ───────────────────────────────────


@dataclass
class ContradictionFlag:
    """Result of a lightweight contradiction check on a new assertion."""

    flagged: bool
    contradicting_entity: str | None = None
    edge_id: int | None = None
    existing_claim_snippet: str | None = None
    review_notes: str | None = None


def check_contradictions(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
) -> ContradictionFlag:
    """Lightweight structural contradiction check at assertion write time.

    1. Extract entity IDs mentioned in the claim text.
    2. For each referenced entity (including the explicit entity_id), check
       for active ``contradicts`` edges.
    3. If a contradiction edge exists, load high-confidence assertions on the
       contradicting entity and flag.

    Returns a ContradictionFlag — callers decide whether to set review_status.
    Performance: O(degree of referenced entities). No global scan.
    """
    mentioned = extract_entity_ids(claim)
    mentioned.add(entity_id)

    for eid in mentioned:
        rows = query(
            conn,
            "SELECT id, from_node, to_node FROM session_edges "
            "WHERE (from_node = ? OR to_node = ?) "
            "AND edge_type = 'contradicts' AND valid_until IS NULL",
            (eid, eid),
        )
        if not rows:
            continue

        for edge_row in rows:
            other_entity = (
                edge_row["to_node"]
                if edge_row["from_node"] == eid
                else edge_row["from_node"]
            )

            high_conf = query(
                conn,
                "SELECT claim FROM assertions "
                "WHERE entity_id = ? AND superseded_by IS NULL "
                "AND confidence IN ('confirmed', 'believed') "
                "ORDER BY created_at DESC LIMIT 1",
                (other_entity,),
            )
            if not high_conf:
                continue

            snippet = high_conf[0]["claim"][:200]
            return ContradictionFlag(
                flagged=True,
                contradicting_entity=other_entity,
                edge_id=edge_row["id"],
                existing_claim_snippet=snippet,
                review_notes=(
                    f"Cross-entity contradiction detected: {eid} contradicts "
                    f"{other_entity} via edge #{edge_row['id']}. "
                    f"Existing belief: {snippet}"
                ),
            )

    return ContradictionFlag(flagged=False)


def extract_entity_ids(text: str) -> set[str]:
    """Extract entity ID patterns (type:slug) from free text."""
    return set(_ENTITY_ID_PATTERN.findall(text))
