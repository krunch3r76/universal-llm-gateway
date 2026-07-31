"""Unit tests for migration 050 (status-trait normalization Phase 0).

Applies the migration to an in-memory SQLite DB seeded with minimal
``entities`` / ``assertions`` tables and confirms:

  * The four entity trait columns (lifecycle, confidence_band,
    confidence_score, adoption) are added, all NULL on existing rows.
  * The assertion ``credibility`` column is added.
  * Existing ``status`` is untouched — Phase 0 changes no reads.
  * Re-running the migration is a no-op (idempotent ALTERs).
  * Absence of ``assertions`` (entities-only sandbox) degrades gracefully.
  * Absence of ``entities`` is a no-op.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "050_status_trait_normalization_phase0.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_050_status_trait_normalization_phase0", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_050 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_050)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unsubstantiated',
            created_at TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL DEFAULT 'believed'
        );
        INSERT INTO entities (id, type, name, status)
        VALUES ('person:test', 'person', 'Test', 'confirmed');
        INSERT INTO assertions (entity_id, claim) VALUES ('person:test', 'c');
        """
    )
    return c


def test_adds_entity_trait_columns(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    cols = _columns(conn, "entities")
    assert {"lifecycle", "confidence_band", "confidence_score", "adoption"} <= cols


def test_adds_assertion_credibility_column(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    assert "credibility" in _columns(conn, "assertions")


def test_existing_rows_have_null_traits_and_preserved_status(
    conn: sqlite3.Connection,
) -> None:
    migration_050.migrate(conn)
    row = conn.execute(
        "SELECT status, lifecycle, confidence_band, confidence_score, adoption "
        "FROM entities WHERE id = 'person:test'"
    ).fetchone()
    assert row["status"] == "confirmed"
    assert row["lifecycle"] is None
    assert row["confidence_band"] is None
    assert row["confidence_score"] is None
    assert row["adoption"] is None


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_050.migrate(conn)
    first = _columns(conn, "entities")
    migration_050.migrate(conn)
    assert _columns(conn, "entities") == first


def test_no_assertions_table_is_graceful() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE entities (id TEXT PRIMARY KEY, type TEXT, name TEXT, "
        "status TEXT);"
    )
    migration_050.migrate(c)
    assert "lifecycle" in _columns(c, "entities")


def test_no_entities_table_is_noop() -> None:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    # No entities table — must not raise.
    migration_050.migrate(c)
