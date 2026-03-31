from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_migration_015():
    path = (
        Path(__file__).resolve().parent / "migrations" / "015_dedup_guard.py"
    )
    spec = importlib.util.spec_from_file_location("migration_015_dedup_guard", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_handles_preexisting_extraction_runs_without_content_hash():
    migration = _load_migration_015()
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE assertions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "entity_id TEXT NOT NULL, "
            "claim TEXT NOT NULL, "
            "superseded_by INTEGER, "
            "review_status TEXT DEFAULT 'committed'"
            ")"
        )
        conn.execute(
            "INSERT INTO assertions (entity_id, claim) VALUES (?, ?)",
            ("entity:test", "Example claim."),
        )
        conn.execute(
            "CREATE TABLE relationships ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "from_entity TEXT NOT NULL, "
            "to_entity TEXT NOT NULL, "
            "type TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE TABLE extraction_runs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "source_uri TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'running', "
            "model TEXT NOT NULL, "
            "chunk_count INTEGER DEFAULT 0, "
            "entity_count INTEGER DEFAULT 0, "
            "assertion_count INTEGER DEFAULT 0, "
            "surface_form_count INTEGER DEFAULT 0, "
            "error_log TEXT, "
            "started_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "completed_at DATETIME, "
            "duration_ms INTEGER"
            ")"
        )
        conn.execute(
            "INSERT INTO extraction_runs (source_uri, status, model) VALUES (?, ?, ?)",
            ("notes/example.md", "running", "test-model"),
        )

        migration.migrate(conn)

        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(extraction_runs)").fetchall()
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(extraction_runs)").fetchall()
        }

        assert "content_hash" in columns
        assert "idx_extraction_runs_source" in indexes
    finally:
        conn.close()
