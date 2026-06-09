"""Migration 055 unit tests — ``session_journals.source_ref`` column add."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = Path(__file__).parent / "migrations" / "055_session_journals_source_ref.py"
_spec = importlib.util.spec_from_file_location(
    "migration_055_session_journals_source_ref", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_055 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_055)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE session_journals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent TEXT NOT NULL,
            summary TEXT NOT NULL,
            domains TEXT,
            decisions TEXT,
            open_items TEXT,
            entity_ids TEXT,
            file_path TEXT,
            session_id TEXT,
            prior_session_id TEXT,
            handoff_prompt TEXT
        );
        """
    )
    return c


def test_migration_adds_source_ref_column(conn: sqlite3.Connection) -> None:
    migration_055.migrate(conn)
    cols = {
        row["name"]: row for row in conn.execute("PRAGMA table_info(session_journals)")
    }
    assert "source_ref" in cols
    assert cols["source_ref"]["type"] == "TEXT"
    assert cols["source_ref"]["notnull"] == 0


def test_migration_is_idempotent(conn: sqlite3.Connection) -> None:
    migration_055.migrate(conn)
    migration_055.migrate(conn)
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")]
    assert cols.count("source_ref") == 1
