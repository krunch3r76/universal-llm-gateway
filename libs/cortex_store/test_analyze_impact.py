"""Contract tests for graph_utils.analyze_impact (Q5 / thread 1174).

Synthetic in-memory graphs only — no live entity IDs or rel counts.
Asserts invariants from tasks/specs/cortex-impact-traversal-substrate-coverage.md
(C1 direction, C2 type set, C3 de-dup, C4 provenance, C5 active predicates).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .graph_utils import analyze_impact

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY, type TEXT, name TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT, claim TEXT, superseded_by INTEGER
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


def _entity(conn: sqlite3.Connection, eid: str, name: str | None = None) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        (eid, eid.split(":", 1)[0], name or eid),
    )


def _rel(
    conn: sqlite3.Connection,
    frm: str,
    to: str,
    type_id: str,
    *,
    active: int = 1,
    valid_until: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, active, valid_until) "
        "VALUES (?, ?, ?, ?, ?)",
        (frm, to, type_id, active, valid_until),
    )


def _session_edge(
    conn: sqlite3.Connection,
    frm: str,
    to: str,
    edge_type: str,
    *,
    valid_until: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO session_edges (from_node, to_node, edge_type, valid_until) "
        "VALUES (?, ?, ?, ?)",
        (frm, to, edge_type, valid_until),
    )


def _impacted_ids(conn: sqlite3.Connection, seed: str, depth: int = 2) -> set[str]:
    return {ie.entity_id for ie in analyze_impact(conn, seed, depth).impacted_entities}


def _impacted_by_id(
    conn: sqlite3.Connection, seed: str, entity_id: str, depth: int = 2
):
    for ie in analyze_impact(conn, seed, depth).impacted_entities:
        if ie.entity_id == entity_id:
            return ie
    return None


def test_structural_requires_surfaces_with_structural_provenance() -> None:
    """Q5 core: structural requires into seed must not return empty."""
    conn = _conn()
    seed = "agent_skill:mcp-surface-change"
    dependent = "todo:dispatch-surface-orientation-fix"
    _entity(conn, seed, "MCP surface change")
    _entity(conn, dependent, "Dispatch surface fix")
    _rel(conn, dependent, seed, "requires")
    conn.commit()

    ie = _impacted_by_id(conn, seed, dependent)
    assert ie is not None
    assert ie.edge_types == ["requires"]
    assert ie.substrates == ["structural"]
    assert ie.hop_distance == 1
    conn.close()


def test_dual_substrate_mirror_deduped_with_both_provenances() -> None:
    conn = _conn()
    seed = "agent_skill:test-skill"
    dependent = "todo:test-dependent"
    _entity(conn, seed)
    _entity(conn, dependent)
    _rel(conn, dependent, seed, "requires")
    _session_edge(conn, dependent, seed, "requires")
    conn.commit()

    result = analyze_impact(conn, seed, depth=1)
    assert len(result.impacted_entities) == 1
    ie = result.impacted_entities[0]
    assert ie.entity_id == dependent
    assert ie.substrates == ["reasoning", "structural"]
    conn.close()


def test_blocked_by_excluded_from_default_blast_radius() -> None:
    """Thread 1174 turn 7: blocked_by is workflow state, not knowledge-propagation."""
    conn = _conn()
    seed = "plan_phase:cursorbuild-mvp/phase-3"
    blocked = "todo:cursorbuild-green-gate-verifies-consumers"
    _entity(conn, seed)
    _entity(conn, blocked)
    _rel(conn, blocked, seed, "blocked_by")
    conn.commit()

    assert blocked not in _impacted_ids(conn, seed)
    conn.close()


def test_forward_dependency_not_collected() -> None:
    """C1: must follow to==seed → collect from; not from==seed inversion."""
    conn = _conn()
    seed = "agent_skill:seed"
    forward = "todo:downstream-of-seed"
    _entity(conn, seed)
    _entity(conn, forward)
    # seed depends on forward — forward is NOT impacted when seed changes
    _rel(conn, seed, forward, "requires")
    conn.commit()

    assert forward not in _impacted_ids(conn, seed)
    conn.close()


def test_reasoning_only_depends_on_has_reasoning_provenance() -> None:
    conn = _conn()
    seed = "decision:seed"
    dependent = "todo:reasoning-dependent"
    _entity(conn, seed)
    _entity(conn, dependent)
    _session_edge(conn, dependent, seed, "depends_on")
    conn.commit()

    ie = _impacted_by_id(conn, seed, dependent)
    assert ie is not None
    assert ie.edge_types == ["depends_on"]
    assert ie.substrates == ["reasoning"]
    conn.close()


def test_inactive_structural_rows_excluded() -> None:
    conn = _conn()
    seed = "agent_skill:seed"
    dependent = "todo:inactive-rel"
    _entity(conn, seed)
    _entity(conn, dependent)
    _rel(conn, dependent, seed, "requires", active=0)
    conn.commit()

    assert dependent not in _impacted_ids(conn, seed)
    conn.close()


def test_retired_session_edges_excluded() -> None:
    conn = _conn()
    seed = "agent_skill:seed"
    dependent = "todo:retired-edge"
    _entity(conn, seed)
    _entity(conn, dependent)
    retired_at = datetime.now(UTC).isoformat()
    _session_edge(conn, dependent, seed, "depends_on", valid_until=retired_at)
    conn.commit()

    assert dependent not in _impacted_ids(conn, seed)
    conn.close()
