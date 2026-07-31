from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops.ops_bulk import (
    _op_entities_bulk_upsert,
    _op_relationships_bulk_upsert,
)


def _seed_relationship_type(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        ("child_of", "Model belongs to family"),
    )
    conn.commit()


def _ensure_entity_aliases_table(conn: sqlite3.Connection) -> None:
    """Some substrate snapshots lack migration-036 ``entity_aliases`` — create for alias tests."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_aliases (
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_id, alias),
            UNIQUE (entity_type, alias)
        )
        """
    )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_relationship_type(migrated_conn)
    _ensure_entity_aliases_table(migrated_conn)
    return migrated_conn


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
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_conn(monkeypatch, conn)
    conn.execute(
        """
        INSERT INTO entities (
            id, type, name, aliases, attributes, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 't0', 't0')
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
    assert "openai/gpt-5.4" in json.loads(
        conn.execute(
            "SELECT aliases FROM entities WHERE id = ?", ("model:gpt-5.4",)
        ).fetchone()[0]
    )


def test_entities_bulk_upsert_rolls_back_on_conflict(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_conn(monkeypatch, conn)
    conn.execute(
        "INSERT INTO entities (id, type, name, created_at, updated_at) "
        "VALUES ('model:existing', 'model', 'Existing', 't0', 't0')"
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
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
