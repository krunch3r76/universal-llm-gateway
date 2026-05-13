from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops.ops_bulk import (
    _op_entities_bulk_upsert,
    _op_relationships_bulk_upsert,
)


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
            status TEXT DEFAULT 'confirmed',
            workflow_state TEXT,
            aliases TEXT,
            attributes TEXT,
            notes TEXT,
            source_uri TEXT,
            content_hash TEXT,
            retention_policy TEXT,
            retention_ttl_days INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE entity_aliases (
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, alias),
            UNIQUE (entity_type, alias)
        );
        CREATE TABLE workflow_schemas (
            entity_type TEXT PRIMARY KEY,
            enum_values TEXT NOT NULL,
            initial_state TEXT NOT NULL,
            terminal_states TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            claim TEXT,
            confidence TEXT,
            confidence_score REAL,
            evidence TEXT,
            evidence_uris TEXT,
            seeded_by TEXT,
            derivation_type TEXT,
            chunk_id INTEGER,
            reasoning_summary TEXT,
            is_atomic INTEGER DEFAULT 1,
            is_decontextualized INTEGER DEFAULT 1,
            observed_at TEXT,
            valid_from TEXT,
            valid_until TEXT,
            superseded_by INTEGER,
            review_status TEXT,
            reviewer TEXT,
            reviewed_at TEXT,
            review_notes TEXT,
            resolution_status TEXT,
            fulfillment_assertion_id INTEGER,
            quality_score REAL,
            prospective_summary TEXT,
            events_json TEXT,
            artifact_uri TEXT,
            artifact_storage TEXT DEFAULT 'inline',
            entrenchment_score REAL,
            predicate_form TEXT,
            created_at TEXT
        );
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            role TEXT,
            strength REAL DEFAULT 1.0,
            evidence TEXT,
            chunk_id INTEGER,
            valid_from TEXT,
            valid_until TEXT,
            source_uri TEXT,
            session_id TEXT,
            agent TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE (from_entity, to_entity, type, active)
        );
        """
    )
    conn.execute(
        "INSERT INTO relationship_types (type, description) VALUES (?, ?)",
        ("child_of", "Model belongs to family"),
    )
    return conn


def _patch_conn(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_bulk_entities.cortex_conn", lambda: conn
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_bulk_relationships.cortex_conn", lambda: conn
    )


def _entity_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT id FROM entities").fetchall()}


def test_entities_bulk_upsert_updates_and_syncs_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _patch_conn(monkeypatch, conn)
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, status, aliases, attributes, created_at, updated_at
        )
        VALUES (?, ?, ?, 'confirmed', ?, ?, 't0', 't0')
        """,
        (
            "model:gpt-5.4",
            "model",
            "GPT-5.4",
            json.dumps(["gpt-5.4-old"]),
            json.dumps({"suitable_for": ["analysis"]}),
        ),
    )
    conn.commit()

    result = _op_entities_bulk_upsert(
        entities=[
            {
                "id": "model:gpt-5.4",
                "type": "model",
                "name": "GPT-5.4",
                "aliases": ["openai/gpt-5.4"],
                "attributes": {"unsuitable_for": ["bulk-write"]},
            },
            {
                "id": "family:openai",
                "type": "family",
                "name": "OpenAI",
                "aliases": ["openai"],
            },
        ],
        if_exists="update",
    )

    assert result["created"] == 1
    assert result["updated"] == 1
    assert _entity_ids(conn) == {"model:gpt-5.4", "family:openai"}
    attrs = json.loads(
        conn.execute(
            "SELECT attributes FROM entities WHERE id = ?", ("model:gpt-5.4",)
        ).fetchone()[0]
    )
    assert attrs == {
        "suitable_for": ["analysis"],
        "unsuitable_for": ["bulk-write"],
    }
    assert (
        conn.execute(
            "SELECT entity_id FROM entity_aliases WHERE alias = ?",
            ("openai/gpt-5.4",),
        ).fetchone()[0]
        == "model:gpt-5.4"
    )


def test_entities_bulk_upsert_rolls_back_on_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _patch_conn(monkeypatch, conn)
    conn.execute(
        "INSERT INTO entities (id, type, name, status, created_at, updated_at) "
        "VALUES ('model:existing', 'model', 'Existing', 'confirmed', 't0', 't0')"
    )
    conn.commit()

    result = _op_entities_bulk_upsert(
        entities=[
            {"id": "model:new", "type": "model", "name": "New"},
            {"id": "model:existing", "type": "model", "name": "Existing"},
        ]
    )

    assert result["rolled_back"] is True
    assert result["failed_index"] == 1
    assert _entity_ids(conn) == {"model:existing"}


def test_relationships_bulk_upsert_resolves_alias_and_updates_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _conn()
    _patch_conn(monkeypatch, conn)
    _op_entities_bulk_upsert(
        entities=[
            {
                "id": "model:gpt-5.4",
                "type": "model",
                "name": "GPT-5.4",
                "aliases": ["openai/gpt-5.4"],
            },
            {"id": "family:openai", "type": "family", "name": "OpenAI"},
        ]
    )

    created = _op_relationships_bulk_upsert(
        relationships=[
            {
                "source_id": "openai/gpt-5.4",
                "target_id": "family:openai",
                "type_id": "child_of",
                "role": "initial",
            }
        ],
        if_exists="update",
    )
    updated = _op_relationships_bulk_upsert(
        relationships=[
            {
                "source_id": "openai/gpt-5.4",
                "target_id": "family:openai",
                "type_id": "child_of",
                "role": "canonical",
            }
        ],
        if_exists="update",
    )

    assert created["items"][0]["resolved_aliases"][0]["entity_id"] == "model:gpt-5.4"
    assert created["items"][0]["action"] == "created"
    assert updated["items"][0]["action"] == "updated"
    rows = conn.execute("SELECT role FROM relationships").fetchall()
    assert [row[0] for row in rows] == ["canonical"]
