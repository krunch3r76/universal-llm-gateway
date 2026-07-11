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
from .edge_walk import active_edges, active_edges_between

logger = get_logger("cortex-api.graph")

# Union dependency set spanning both substrates (cortex-spec §§8–9 + migration
# 041). Knowledge-propagation semantics only: "if Y changes, what is downstream
# and should be re-examined?". `blocked_by` is deliberately EXCLUDED — it is
# workflow/scheduling state ("A waits on B"), not content/validity dependency
# (thread 1174 turn 7; cortex:notes/system/threads/1174-union-implementation-notes.md).
# A workflow view ("what is waiting on this?") would be an explicit opt-in, never
# the default blast-radius set. Types absent from a substrate simply yield no rows
# there; graph_utils does not validate against a type registry, so the shared set
# is safe for both the structural `relationships` and reasoning `session_edges`
# halves.
_DEPENDENCY_EDGE_TYPES = (
    "requires",
    "depends_on",
    "derived_from",
    "evidence_for",
    "extends",
)

_ENTITY_ID_PATTERN = re.compile(
    r"(?<![\w-])([a-z][a-z_]*:[a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE
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
    substrates: list[str]


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
    """BFS from *seed_entity* over dependency edges across BOTH substrates.

    Reverse-dependency closure (cortex-spec §§8–9, contract C1): an edge
    ``X --type--> Y`` means X depends on Y, so when Y (seed) changes X is
    impacted.  We follow edges where the *target* is the current node and
    collect the *source* as the impacted dependent — uniformly over the
    structural ``relationships`` table and the reasoning ``session_edges``
    table (migration 041 dual-registration).  Each impacted entity carries
    the substrate(s) its dependency edge came from: ``structural`` is
    consensus ground truth, ``reasoning`` is session-attributed.

    Depth is clamped to [1, 5].  Cycle detection via visited set.
    """
    depth = max(1, min(depth, 5))

    visited: set[str] = {seed_entity}
    # Each entry: (entity_id, hop_distance, path from seed)
    frontier: list[tuple[str, int, list[str]]] = [(seed_entity, 0, [seed_entity])]
    impacted: list[ImpactedEntity] = []
    total_assertions = 0

    for _ in range(depth):
        next_frontier: list[tuple[str, int, list[str]]] = []

        for entity_id, hop, path in frontier:
            for dep in _dependency_sources(conn, entity_id):
                if dep in visited:
                    continue
                visited.add(dep)
                next_frontier.append((dep, hop + 1, [*path, dep]))

        for entity_id, hop, path in next_frontier:
            entity_name = _entity_name(conn, entity_id)
            a_count = _active_assertion_count(conn, entity_id)
            total_assertions += a_count
            edge_types_used, substrates_used = _path_edges(conn, path)

            impacted.append(
                ImpactedEntity(
                    entity_id=entity_id,
                    entity_name=entity_name,
                    hop_distance=hop,
                    path_trace=path,
                    assertion_count=a_count,
                    edge_types=sorted(set(edge_types_used)),
                    substrates=sorted(set(substrates_used)),
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


def _dependency_sources(conn: sqlite3.Connection, node: str) -> list[str]:
    """Distinct source entities of incoming dependency edges into *node*.

    Reverse-dependency direction (contract C1): edges whose *target* is
    ``node``, returning the *source* (the impacted dependent), unioned across
    both substrates with their per-substrate active predicates (contract C5) via
    the shared :func:`edge_walk.active_edges` primitive.  De-dups neighbors
    first-seen — the visited set in :func:`analyze_impact` makes order
    immaterial; ``_path_edges`` recomputes per-edge substrate provenance
    separately.
    """
    edges = active_edges(conn, node, types=_DEPENDENCY_EDGE_TYPES, direction="reverse")
    return list(dict.fromkeys(e.neighbor for e in edges))


def _path_edges(
    conn: sqlite3.Connection,
    path: list[str],
) -> tuple[list[str], list[str]]:
    """Edge types + substrates traversed along *path* (seed → impacted).

    Path goes seed → impacted, but dependency edges point impacted → seed
    (``from`` depends on ``to``).  So for consecutive path nodes [A, B] the
    edge is ``from = B, to = A``.  Unions both substrates (contract C6) and
    returns parallel ``(types, substrates)`` lists for provenance aggregation.
    Delegates to :func:`edge_walk.active_edges_between` to avoid mirroring the
    per-substrate active predicates here ([universal:libs-first]).
    """
    types: list[str] = []
    substrates: list[str] = []
    for i in range(len(path) - 1):
        frm, to = path[i + 1], path[i]
        for edge in active_edges_between(conn, frm, to, types=_DEPENDENCY_EDGE_TYPES):
            types.append(edge.edge_type)
            substrates.append(edge.substrate)
    return types, substrates


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
