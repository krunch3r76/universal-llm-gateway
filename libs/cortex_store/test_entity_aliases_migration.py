from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from cortex_store.entity_aliases import resolve_entity_reference, sync_entity_aliases


def _load_migration_056():
    path = Path(__file__).resolve().parent / "migrations" / "056_entity_aliases.py"
    spec = importlib.util.spec_from_file_location("migration_056_entity_aliases", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT NOT NULL, aliases TEXT)"
    )
    return conn


def test_entity_aliases_migration_backfills_unique_aliases() -> None:
    migration = _load_migration_056()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO entities (id, type, aliases) VALUES (?, ?, ?)",
            ("model:gpt-5.4", "model", json.dumps(["openai/gpt-5.4"])),
        )
        conn.execute(
            "INSERT INTO entities (id, type, aliases) VALUES (?, ?, ?)",
            ("family:openai", "family", json.dumps(["openai"])),
        )

        migration.migrate(conn)

        rows = conn.execute(
            "SELECT entity_id, entity_type, alias FROM entity_aliases ORDER BY alias"
        ).fetchall()
        assert rows == [
            ("family:openai", "family", "openai"),
            ("model:gpt-5.4", "model", "openai/gpt-5.4"),
        ]
    finally:
        conn.close()


def test_entity_aliases_migration_first_wins_cross_entity_collisions() -> None:
    migration = _load_migration_056()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO entities (id, type, aliases) VALUES (?, ?, ?)",
            ("model:a", "model", json.dumps(["shared"])),
        )
        conn.execute(
            "INSERT INTO entities (id, type, aliases) VALUES (?, ?, ?)",
            ("model:b", "model", json.dumps(["shared"])),
        )

        migration.migrate(conn)

        rows = conn.execute(
            "SELECT entity_id, entity_type, alias FROM entity_aliases"
        ).fetchall()
        assert rows == [("model:a", "model", "shared")]
    finally:
        conn.close()


def test_alias_helpers_tolerate_pre_migration_database() -> None:
    conn = _conn()
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO entities (id, type, aliases) VALUES (?, ?, ?)",
            ("model:gpt-5.4", "model", json.dumps(["openai/gpt-5.4"])),
        )

        sync_entity_aliases(
            conn,
            entity_id="model:gpt-5.4",
            entity_type="model",
            aliases=["openai/gpt-5.4"],
        )
        resolved = resolve_entity_reference(
            conn,
            "model:gpt-5.4",
            resolve_aliases=True,
            label="source",
        )

        assert resolved.entity_id == "model:gpt-5.4"
    finally:
        conn.close()
