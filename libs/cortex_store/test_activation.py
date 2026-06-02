"""Contract tests for activation.spreading_activation (Q5 sibling, thread 1174).

Synthetic in-memory graphs only — no live entity IDs or rel counts.
Asserts invariants from tasks/specs/cortex-activate-substrate-coverage.md
(D1 type set + both-substrate union, D2 hub-denominator reconciliation).
"""

from __future__ import annotations

import sqlite3

from .activation import (
    _entity_edge_degree,
    _total_active_edge_count,
    spreading_activation,
)

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY, type TEXT, name TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT, claim TEXT, confidence TEXT,
    entrenchment_score REAL, superseded_by INTEGER
);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity TEXT, to_entity TEXT, type TEXT,
    active INTEGER DEFAULT 1, valid_until TEXT
);
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node TEXT, to_node TEXT, edge_type TEXT, valid_until TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _entity(conn: sqlite3.Connection, eid: str) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        (eid, eid.split(":", 1)[0], eid),
    )


def _assertion(
    conn: sqlite3.Connection, eid: str, claim: str, score: float = 0.9
) -> None:
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, entrenchment_score, "
        "superseded_by) VALUES (?, ?, 'believed', ?, NULL)",
        (eid, claim, score),
    )


def _rel(
    conn: sqlite3.Connection, frm: str, to: str, type_id: str, *, active: int = 1
) -> None:
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, valid_until) "
        "VALUES (?, ?, ?, ?, NULL)",
        (frm, to, type_id, active),
    )


def _session_edge(conn: sqlite3.Connection, frm: str, to: str, edge_type: str) -> None:
    conn.execute(
        "INSERT INTO session_edges (from_node, to_node, edge_type, valid_until) "
        "VALUES (?, ?, ?, NULL)",
        (frm, to, edge_type),
    )


def _activated_entities(
    conn: sqlite3.Connection, seed: str, **kw
) -> dict[str, list[str]]:
    """Map activated entity_id -> substrates_traversed (hub suppression off)."""
    kw.setdefault("suppress_hubs", False)
    result = spreading_activation(conn, [seed], depth=1, **kw)
    return {a.entity_id: a.substrates_traversed for a in result.activated}


def test_structural_requires_neighbor_surfaces() -> None:
    """Q5 sibling core: a neighbor reachable only via structural requires must surface."""
    conn = _conn()
    seed, dep = "agent_skill:mcp-surface-change", "todo:dispatch-fix"
    _entity(conn, seed)
    _entity(conn, dep)
    _assertion(conn, dep, "dependent assertion")
    _rel(conn, dep, seed, "requires")
    conn.commit()

    activated = _activated_entities(conn, seed)
    assert dep in activated
    assert "structural" in activated[dep]
    conn.close()


def test_structural_child_of_neighbor_surfaces() -> None:
    conn = _conn()
    seed, child = "task:parent", "todo:child"
    _entity(conn, seed)
    _entity(conn, child)
    _assertion(conn, child, "child assertion")
    _rel(conn, child, seed, "child_of")
    conn.commit()

    assert child in _activated_entities(conn, seed)
    conn.close()


def test_blocked_by_excluded() -> None:
    """D1 uniform invariant: blocked_by is workflow state, never in the default spread."""
    conn = _conn()
    seed, blocked = "plan_phase:p3", "todo:blocked"
    _entity(conn, seed)
    _entity(conn, blocked)
    _assertion(conn, blocked, "blocked assertion")
    _rel(conn, blocked, seed, "blocked_by")
    conn.commit()

    assert blocked not in _activated_entities(conn, seed)
    conn.close()


def test_reasoning_relates_to_preserved() -> None:
    """Existing reasoning-substrate behavior must be preserved."""
    conn = _conn()
    seed, neighbor = "decision:seed", "decision:related"
    _entity(conn, seed)
    _entity(conn, neighbor)
    _assertion(conn, neighbor, "related assertion")
    _session_edge(conn, seed, neighbor, "relates_to")
    conn.commit()

    activated = _activated_entities(conn, seed)
    assert neighbor in activated
    assert "reasoning" in activated[neighbor]
    conn.close()


def test_reasoning_requires_now_walked() -> None:
    """D1: requires was omitted from the old reasoning set; now walked on session_edges."""
    conn = _conn()
    seed, dep = "agent_skill:seed", "todo:reasoning-requires"
    _entity(conn, seed)
    _entity(conn, dep)
    _assertion(conn, dep, "reasoning requires assertion")
    _session_edge(conn, dep, seed, "requires")
    conn.commit()

    assert dep in _activated_entities(conn, seed)
    conn.close()


def test_derived_from_now_walked() -> None:
    conn = _conn()
    seed, deriv = "entity:source", "entity:derivative"
    _entity(conn, seed)
    _entity(conn, deriv)
    _assertion(conn, deriv, "derivative assertion")
    _session_edge(conn, deriv, seed, "derived_from")
    conn.commit()

    assert deriv in _activated_entities(conn, seed)
    conn.close()


def test_hub_degree_counts_both_substrates() -> None:
    """D2: degree + denominator union both substrates so hub-ness is coherent."""
    conn = _conn()
    hub = "entity:hub"
    _entity(conn, hub)
    for i in range(3):
        _entity(conn, f"entity:r{i}")
        _rel(conn, hub, f"entity:r{i}", "related_to")
    for i in range(2):
        _entity(conn, f"entity:s{i}")
        _session_edge(conn, hub, f"entity:s{i}", "relates_to")
    # one inactive structural rel must NOT count
    _entity(conn, "entity:inactive")
    _rel(conn, hub, "entity:inactive", "related_to", active=0)
    conn.commit()

    assert _entity_edge_degree(conn, hub) == 5  # 3 structural + 2 reasoning
    assert _total_active_edge_count(conn) == 5
    conn.close()
