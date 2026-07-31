"""Tests for edge endpoint validation per v2.3 §3.4.

Pins the two-table behaviour of ``_endpoint_resolves``:

- ``entity_id`` (any prefix shape) → ``entities`` table
- ``assertion:N``                  → ``assertions`` table

Companion to ``todo:cortex-edge-endpoint-namespaced-id-validation``. The bug
(diagnosed in assertion 8274, observed via ``edge_create`` rejection of
``assertion:N`` endpoints) was that the validator only consulted ``entities``,
breaking assertion-as-evidence and assertion-to-assertion provenance edges
the spec declares valid.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cortex_store.models import EdgeUpdate
from cortex_store.routes.edges import _endpoint_resolves, retire_edge, update_edge

_EDGE_SCHEMA = """
CREATE TABLE session_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    from_node TEXT NOT NULL,
    to_node TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    strength REAL NOT NULL DEFAULT 0.8,
    edge_source TEXT NOT NULL DEFAULT 'explicit',
    context TEXT,
    prompt TEXT,
    seeded_by TEXT,
    valid_until TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _conn() -> sqlite3.Connection:
    """In-memory cortex schema slice — entities + assertions only."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL
        );
        INSERT INTO entities (id, type, name) VALUES
            ('service:cortex',     'service',     'cortex'),
            ('todo:foo',           'todo',        'foo'),
            ('agent_skill:bar',    'agent_skill', 'bar'),
            ('event:e1',           'event',       'e1'),
            ('doc:d1',             'document',    'd1'),
            ('plain-id-no-prefix', 'misc',        'plain');
        INSERT INTO assertions (id, entity_id) VALUES
            (8212, 'service:cortex'),
            (8276, 'service:cortex');
        """
    )
    return conn


def _edge_conn() -> sqlite3.Connection:
    """In-memory schema slice for session-edge CRUD route tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_EDGE_SCHEMA)
    return conn


def _insert_edge(
    conn: sqlite3.Connection,
    *,
    session_id: str = "claude-cursor-2026-06-21-1200",
    agent: str = "claude-cursor",
    from_node: str = "service:cortex",
    to_node: str = "todo:foo",
    edge_type: str = "extends",
    strength: float = 0.5,
    context: str | None = "ctx",
    prompt: str | None = "prompt",
    seeded_by: str | None = "dream:seed-1",
    metadata: str | None = '{"k": "v"}',
    valid_until: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type, "
        "strength, edge_source, context, prompt, seeded_by, valid_until, metadata, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'explicit', ?, ?, ?, ?, ?, ?)",
        (
            session_id,
            agent,
            from_node,
            to_node,
            edge_type,
            strength,
            context,
            prompt,
            seeded_by,
            valid_until,
            metadata,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


@pytest.fixture
def edge_conn() -> sqlite3.Connection:
    return _edge_conn()


def test_resolves_entity_ids_with_any_prefix() -> None:
    """Per assertion 8274: any prefix shape resolves through entities when
    the row exists. The colon is a primary-key convention, not a namespace."""
    conn = _conn()
    assert _endpoint_resolves(conn, "service:cortex")
    assert _endpoint_resolves(conn, "todo:foo")
    assert _endpoint_resolves(conn, "agent_skill:bar")
    assert _endpoint_resolves(conn, "event:e1")
    assert _endpoint_resolves(conn, "doc:d1")
    assert _endpoint_resolves(conn, "plain-id-no-prefix")


def test_resolves_assertion_ids() -> None:
    """The fix: ``assertion:N`` now resolves against the ``assertions`` table,
    enabling assertion-as-evidence and assertion-to-assertion edges."""
    conn = _conn()
    assert _endpoint_resolves(conn, "assertion:8212")
    assert _endpoint_resolves(conn, "assertion:8276")


def test_rejects_missing_entity_ids() -> None:
    conn = _conn()
    assert not _endpoint_resolves(conn, "todo:does-not-exist")
    assert not _endpoint_resolves(conn, "service:phantom")
    assert not _endpoint_resolves(conn, "agent_skill:imaginary")


def test_rejects_missing_assertion_ids() -> None:
    conn = _conn()
    assert not _endpoint_resolves(conn, "assertion:99999")


def test_rejects_malformed_assertion_addresses() -> None:
    """Non-integer suffixes (and empty suffix) fall to False rather than
    raising — the caller gets a clean ``dangling_edge`` error instead of an
    integer-cast exception leaking through."""
    conn = _conn()
    assert not _endpoint_resolves(conn, "assertion:not-an-int")
    assert not _endpoint_resolves(conn, "assertion:")
    assert not _endpoint_resolves(conn, "assertion:8212.5")
    assert not _endpoint_resolves(conn, "assertion:1e5")


def test_edge_update_patches_strength_in_place(edge_conn: sqlite3.Connection) -> None:
    edge_id = _insert_edge(edge_conn, strength=0.5)
    with patch("cortex_store.routes.edges.cortex_conn", return_value=edge_conn):
        item = update_edge(edge_id, EdgeUpdate(strength=0.9))
    assert item.strength == 0.9
    assert item.id == edge_id


def test_edge_update_noop_on_retired_edge_returns_200_unchanged(
    edge_conn: sqlite3.Connection,
) -> None:
    edge_id = _insert_edge(edge_conn, strength=0.5)
    with patch("cortex_store.routes.edges.cortex_conn", return_value=edge_conn):
        retired = retire_edge(edge_id, None)
        assert retired.valid_until is not None
        item = update_edge(edge_id, EdgeUpdate(strength=0.99))
    assert item.strength == 0.5
    assert item.valid_until is not None


def test_edge_update_404_on_missing_id(edge_conn: sqlite3.Connection) -> None:
    with patch("cortex_store.routes.edges.cortex_conn", return_value=edge_conn):
        with pytest.raises(HTTPException) as exc:
            update_edge(99999, EdgeUpdate(strength=0.9))
    assert exc.value.status_code == 404


def test_edge_update_preserves_seeded_by_session_id_agent(
    edge_conn: sqlite3.Connection,
) -> None:
    session_id = "claude-cursor-2026-06-21-1300"
    agent = "claude-cursor"
    seeded_by = "dream:provenance-anchor"
    edge_id = _insert_edge(
        edge_conn,
        session_id=session_id,
        agent=agent,
        seeded_by=seeded_by,
        strength=0.4,
    )
    with patch("cortex_store.routes.edges.cortex_conn", return_value=edge_conn):
        item = update_edge(edge_id, EdgeUpdate(strength=0.75, context="patched"))
    assert item.session_id == session_id
    assert item.agent == agent
    assert item.seeded_by == seeded_by
    assert item.strength == 0.75
    assert item.context == "patched"


def test_edge_update_rejects_empty_patch_422(edge_conn: sqlite3.Connection) -> None:
    edge_id = _insert_edge(edge_conn)
    with patch("cortex_store.routes.edges.cortex_conn", return_value=edge_conn):
        with pytest.raises(HTTPException) as exc:
            update_edge(edge_id, EdgeUpdate())
    assert exc.value.status_code == 422
    assert exc.value.detail == "No fields to update"
