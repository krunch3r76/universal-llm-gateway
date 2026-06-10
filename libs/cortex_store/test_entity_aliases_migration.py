from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

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
        "CREATE TABLE entities"
        " (id TEXT PRIMARY KEY, type TEXT NOT NULL, aliases TEXT, lifecycle TEXT)"
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


def test_merged_entity_excluded_from_backfill() -> None:
    """Active entity wins alias even when the merged stub has a lex-smaller id."""
    migration = _load_migration_056()
    conn = _conn()
    try:
        # "model:a-merged" < "model:b-active" — without the lifecycle filter
        # the stub wins the first-wins tiebreak.
        conn.execute(
            "INSERT INTO entities (id, type, aliases, lifecycle) VALUES (?, ?, ?, ?)",
            ("model:a-merged", "model", json.dumps(["shared"]), "merged"),
        )
        conn.execute(
            "INSERT INTO entities (id, type, aliases, lifecycle) VALUES (?, ?, ?, ?)",
            ("model:b-active", "model", json.dumps(["shared"]), None),
        )

        migration.migrate(conn)

        rows = conn.execute(
            "SELECT entity_id FROM entity_aliases WHERE alias = 'shared'"
        ).fetchall()
        assert len(rows) == 1, "exactly one winner expected"
        assert rows[0][0] == "model:b-active"
    finally:
        conn.close()


def test_null_lifecycle_treated_as_live() -> None:
    """NULL lifecycle is the active default; its aliases must appear in entity_aliases."""
    migration = _load_migration_056()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO entities (id, type, aliases, lifecycle) VALUES (?, ?, ?, ?)",
            ("model:gpt-null", "model", json.dumps(["null-lifecycle-alias"]), None),
        )

        migration.migrate(conn)

        rows = conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = 'model:gpt-null'"
        ).fetchall()
        assert rows == [("null-lifecycle-alias",)], (
            "NULL lifecycle must be treated as live; alias must be indexed"
        )
    finally:
        conn.close()


def test_sync_skips_non_live_entity() -> None:
    """sync_entity_aliases clears rows and inserts nothing for non-live entities."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY, type TEXT NOT NULL,
            aliases TEXT, lifecycle TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE entity_aliases (
            entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            entity_type TEXT NOT NULL,
            alias TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (entity_id, alias),
            UNIQUE (entity_type, alias)
        )
        """
    )
    conn.execute(
        "INSERT INTO entities (id, type, aliases, lifecycle) VALUES (?, ?, ?, ?)",
        ("person:merged-stub", "person", json.dumps(["Old Name"]), "merged"),
    )
    # Pre-seed a stale alias row as if migration 056 ran without the filter.
    conn.execute(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias)"
        " VALUES ('person:merged-stub', 'person', 'Old Name')"
    )
    try:
        sync_entity_aliases(
            conn,
            entity_id="person:merged-stub",
            entity_type="person",
            aliases=["Old Name"],
            lifecycle="merged",
        )

        rows = conn.execute("SELECT * FROM entity_aliases").fetchall()
        assert list(rows) == [], "merged entity must not hold alias rows"
    finally:
        conn.close()
