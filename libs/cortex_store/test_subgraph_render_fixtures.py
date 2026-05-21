"""Shared in-memory fixtures for subgraph render tests.

Factored out of ``test_subgraph_render.py`` so the test module stays
under the modularize SLOC ceiling. Schema mirrors the cortex_store
production schema for the subset of tables the renderer reads.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE entities (
    id TEXT PRIMARY KEY, type TEXT, name TEXT, description TEXT,
    status TEXT, workflow_state TEXT, attributes TEXT,
    source_uri TEXT, content_hash TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, claim TEXT,
    confidence TEXT, superseded_by INTEGER, observed_at TEXT,
    entrenchment_score REAL, evidence TEXT, derivation_type TEXT,
    valid_from TEXT, predicate_form TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT, from_entity TEXT,
    to_entity TEXT, type TEXT, role TEXT, strength REAL,
    active INTEGER DEFAULT 1, valid_until TEXT
);
CREATE TABLE relationship_types (type TEXT PRIMARY KEY, description TEXT);
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node TEXT, to_node TEXT, valid_until TEXT
);
CREATE TABLE entity_access_log (
    entity_id TEXT, agent TEXT, operation TEXT,
    source TEXT, session_id TEXT,
    ts TEXT DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO relationship_types (type, description) VALUES
    ('depends_on', ''), ('references', ''),
    ('related_to', ''), ('archives_to', '');
"""


def make_test_conn(path: str = ":memory:") -> sqlite3.Connection:
    """Build a SQLite connection with the renderer's required schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def add_entity(
    conn: sqlite3.Connection,
    eid: str,
    etype: str = "todo",
    name: str | None = None,
    description: str = "",
    status: str = "confirmed",
    workflow_state: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, description, status, "
        "workflow_state, attributes, source_uri, content_hash, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            eid,
            etype,
            name if name is not None else eid,
            description,
            status,
            workflow_state,
            None,
            None,
            None,
            now,
            now,
        ),
    )


def add_edge(
    conn: sqlite3.Connection,
    src: str,
    tgt: str,
    type_id: str = "depends_on",
    role: str | None = None,
    strength: float = 1.0,
) -> None:
    conn.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, role, "
        "strength, active, valid_until) VALUES (?,?,?,?,?,1,NULL)",
        (src, tgt, type_id, role, strength),
    )


def add_assertion(
    conn: sqlite3.Connection,
    eid: str,
    claim: str,
    confidence: str = "confirmed",
    superseded_by: int | None = None,
    entrenchment: float = 1.0,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, "
        "superseded_by, observed_at, entrenchment_score) "
        "VALUES (?,?,?,?,?,?)",
        (eid, claim, confidence, superseded_by, now, entrenchment),
    )


def seed_grokbuild_graph(conn: sqlite3.Connection) -> None:
    """Six-entity test graph rooted at the grokbuild decision.

    Layout:
        root -> v1, v2, v3, v4   (depends_on, outbound)
        v1 -> v2                 (sibling edge at hop 1)
        decision:other -> root   (references, inbound)
    """
    root = "decision:grokbuild-cursor-alternative"
    add_entity(conn, root, "decision", "Grokbuild Cursor Alt", "Root.")
    for i in range(1, 5):
        add_entity(conn, f"todo:grokbuild-v{i}", "todo", f"Grokbuild V{i}", "Phase.")
    add_entity(conn, "decision:other", "decision", "Other", "Cross edge.")
    add_assertion(conn, root, "Root claim active.", "confirmed", None, 10.0)
    add_assertion(conn, root, "Superseded root claim.", "believed", 1, 1.0)
    add_assertion(conn, "todo:grokbuild-v1", "V1 done.", "confirmed", None, 5.0)
    for i in range(1, 5):
        add_edge(conn, root, f"todo:grokbuild-v{i}", "depends_on")
    add_edge(conn, "todo:grokbuild-v1", "todo:grokbuild-v2", "depends_on", strength=0.8)
    add_edge(conn, "decision:other", root, "references")
    conn.commit()


def init_temp_db(db_path: str) -> None:
    """Create an empty schema-only DB at ``db_path``. Used for route tests."""
    conn = make_test_conn(db_path)
    conn.commit()
    conn.close()
