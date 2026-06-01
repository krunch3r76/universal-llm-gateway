"""Tests for GET /api/v1/entities/source-paths (EntityAdmissionGate snapshot)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from cortex_store.routes.entities import list_entity_source_paths

_FILES_ROOT = "/mnt/torus/mcp-data/files"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT,
            workflow_state TEXT,
            aliases TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT,
            attributes TEXT
        );
        """
    )
    return conn


@contextmanager
def _patch_conn(conn: sqlite3.Connection):
    @contextmanager
    def fake_cortex_conn():
        yield conn

    with patch("cortex_store.routes.entities.cortex_conn", fake_cortex_conn):
        yield


def test_source_paths_dedupes_plain_relative() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, status, source_uri, created_at) "
        "VALUES ('doc:a', 'document', 'A', 'confirmed', ?, '2026-06-01T00:00:00Z')",
        ("agent-skills/foo.md",),
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, status, source_uri, created_at) "
        "VALUES ('doc:b', 'document', 'B', 'confirmed', ?, '2026-06-01T00:00:00Z')",
        ("agent-skills/foo.md",),
    )
    conn.commit()

    with (
        _patch_conn(conn),
        patch("cortex_store.rag_resolver._FILES_ROOT", _FILES_ROOT),
    ):
        resp = list_entity_source_paths()

    expected = f"{_FILES_ROOT}/agent-skills/foo.md"
    assert resp.count == 1
    assert resp.paths == [expected]
    assert resp.unresolved == 0


def test_source_paths_unresolved_increments_without_500() -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO entities (id, type, name, status, source_uri, created_at) "
        "VALUES ('doc:bad', 'document', 'Bad', 'confirmed', ?, '2026-06-01T00:00:00Z')",
        ("cortex://agent_skill/missing",),
    )
    conn.commit()

    with (
        _patch_conn(conn),
        patch(
            "cortex_store.rag_resolver._source_uri_to_absolute_path",
            side_effect=ValueError("missing entity"),
        ),
    ):
        resp = list_entity_source_paths()

    assert resp.paths == []
    assert resp.count == 0
    assert resp.unresolved == 1
