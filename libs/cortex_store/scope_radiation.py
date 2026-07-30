"""Pure scope radiation walk for hub entity_get (arc 6386).

Root in → scoped entity set with hop distance and admitting-path metadata out.
No terminal-facts-specific logic — promotion-ready primitive.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass

from .db import query
from .subgraph_traversal import WalkDirection, archived_set

HUB_SCOPE_ENTITY_CAP = 50
HUB_REL_THRESHOLD = 20
DEFAULT_HOPS = 2

# (edge_type, target_kind) — target_kind None denies all targets for that edge type.
PATH_DENY_PAIRS: tuple[tuple[str, str | None], ...] = (
    ("requires", "rule"),
    ("requires", "agent_skill"),
    ("has_playbook", None),
    ("child_of", "todo"),
    ("references", "transcript"),
)

PARTY_KIND_PREFIXES = ("org:", "person:")


@dataclass(frozen=True)
class RadiatedScope:
    hop_distances: dict[str, int]
    arrival_paths: dict[str, list[str]]
    truncated: bool


def entity_kind(entity_id: str) -> str:
    if ":" not in entity_id:
        return entity_id
    return entity_id.split(":", 1)[0]


def is_party_entity(entity_id: str) -> bool:
    return entity_id.startswith(PARTY_KIND_PREFIXES)


def edge_path_denied(edge_type: str, target_id: str) -> bool:
    target_kind = entity_kind(target_id)
    for deny_type, deny_kind in PATH_DENY_PAIRS:
        if edge_type != deny_type:
            continue
        if deny_kind is None or deny_kind == target_kind:
            return True
    return False


def _relationship_count(conn: sqlite3.Connection, entity_id: str) -> int:
    row = query(
        conn,
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT 1 FROM relationships "
        "  WHERE from_entity = ? AND active = 1 AND valid_until IS NULL "
        "  UNION ALL "
        "  SELECT 1 FROM relationships "
        "  WHERE to_entity = ? AND active = 1 AND valid_until IS NULL"
        ")",
        (entity_id, entity_id),
    )
    return int(row[0]["n"]) if row else 0


def radiate_scope(
    conn: sqlite3.Connection,
    root: str,
    *,
    hops: int | None = None,
    entity_cap: int | None = None,
    hub_rel_threshold: int | None = None,
    direction: WalkDirection = "both",
) -> RadiatedScope:
    """N-hop BFS from root with typed path bounds; degrades on entity cap."""
    hop_limit = hops if hops is not None else DEFAULT_HOPS
    cap = entity_cap if entity_cap is not None else HUB_SCOPE_ENTITY_CAP
    hub_threshold = (
        hub_rel_threshold if hub_rel_threshold is not None else HUB_REL_THRESHOLD
    )
    archived = archived_set(conn)
    hop_distances: dict[str, int] = {root: 0}
    arrival_paths: dict[str, list[str]] = {root: [root]}
    truncated = False
    q: deque[tuple[str, int]] = deque([(root, 0)])

    while q:
        curr, depth = q.popleft()
        if depth >= hop_limit:
            continue
        if depth > 0 and _relationship_count(conn, curr) >= hub_threshold:
            continue
        if is_party_entity(curr):
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
            if direction == "outbound" and src != curr:
                continue
            if direction == "inbound" and tgt != curr:
                continue
            neigh = tgt if src == curr else src
            if neigh in hop_distances or neigh in archived or src == tgt:
                continue
            if edge_path_denied(typ, neigh):
                continue
            if len(hop_distances) >= cap:
                truncated = True
                return RadiatedScope(hop_distances, arrival_paths, truncated)
            hop_distances[neigh] = depth + 1
            arrival_paths[neigh] = arrival_paths[curr] + [neigh]
            if not is_party_entity(neigh):
                q.append((neigh, depth + 1))

    return RadiatedScope(hop_distances, arrival_paths, truncated)
