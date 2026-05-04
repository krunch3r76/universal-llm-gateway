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

from cortex_store.routes.edges import _endpoint_resolves


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
