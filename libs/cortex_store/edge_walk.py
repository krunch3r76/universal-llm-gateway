"""Shared neighbor primitive — active edges incident to a node across BOTH
cortex substrates.

Reconciles the structural ``relationships`` and reasoning ``session_edges``
tables at the read layer (``[universal:libs-first]``) so traversal primitives
walk one unified edge view instead of a single substrate each:

  * ``graph_utils.analyze_impact`` — reverse-dependency BFS (``direction="reverse"``)
  * ``activation.spreading_activation`` — undirected spread (``direction="both"``)

Per-substrate active predicates (cortex-spec §§8–9, migration 041; thread 1174):
``session_edges`` is active when ``valid_until IS NULL``; ``relationships`` when
``active = 1 AND valid_until IS NULL``.  A type absent from a substrate yields no
rows there (no registry validation), so the same *types* set is safe for both
halves.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .db import query

Direction = Literal["reverse", "both"]


@dataclass(frozen=True)
class ActiveEdge:
    """An active edge incident to a query node.

    ``neighbor`` is the endpoint opposite the query node:
      * ``direction="reverse"`` — edges whose *target* is the node; neighbor =
        the *source* (the reverse-dependency dependent).
      * ``direction="both"`` — edges with the node on either end; neighbor = the
        opposite endpoint.
    """

    neighbor: str
    edge_type: str
    substrate: str  # "structural" | "reasoning"


def active_edges(
    conn: sqlite3.Connection,
    node: str,
    *,
    types: Sequence[str],
    direction: Direction,
) -> list[ActiveEdge]:
    """Active edges incident to *node* across both substrates, filtered to *types*.

    ``UNION ALL`` (no SQL-level de-dup): callers that need distinct neighbors
    de-dup on ``neighbor`` themselves, and a substrate-mirrored edge legitimately
    contributes one row per substrate.
    """
    if not types:
        return []
    type_ph = ",".join("?" for _ in types)
    types_t = tuple(types)

    if direction == "reverse":
        sql = (
            f"SELECT from_node AS neighbor, edge_type AS etype, 'reasoning' AS substrate "
            f"FROM session_edges "
            f"WHERE to_node = ? AND edge_type IN ({type_ph}) AND valid_until IS NULL "
            f"UNION ALL "
            f"SELECT from_entity AS neighbor, type AS etype, 'structural' AS substrate "
            f"FROM relationships "
            f"WHERE to_entity = ? AND type IN ({type_ph}) "
            f"AND active = 1 AND valid_until IS NULL"
        )
        params = (node, *types_t, node, *types_t)
        return [
            ActiveEdge(str(r["neighbor"]), str(r["etype"]), str(r["substrate"]))
            for r in query(conn, sql, params)
        ]

    # direction == "both": node may be on either end; neighbor = the other end.
    sql = (
        f"SELECT from_node, to_node, edge_type AS etype, 'reasoning' AS substrate "
        f"FROM session_edges "
        f"WHERE (from_node = ? OR to_node = ?) AND edge_type IN ({type_ph}) "
        f"AND valid_until IS NULL "
        f"UNION ALL "
        f"SELECT from_entity AS from_node, to_entity AS to_node, type AS etype, "
        f"'structural' AS substrate "
        f"FROM relationships "
        f"WHERE (from_entity = ? OR to_entity = ?) AND type IN ({type_ph}) "
        f"AND active = 1 AND valid_until IS NULL"
    )
    params = (node, node, *types_t, node, node, *types_t)
    edges: list[ActiveEdge] = []
    for r in query(conn, sql, params):
        frm = r["from_node"]
        neighbor = r["to_node"] if frm == node else frm
        edges.append(ActiveEdge(str(neighbor), str(r["etype"]), str(r["substrate"])))
    return edges
