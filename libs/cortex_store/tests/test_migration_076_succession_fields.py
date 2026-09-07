"""Migration 076 unit tests — succession columns on session_journals."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = Path(__file__).parent.parent / "migrations" / "076_session_journals_succession_fields.py"
_spec = importlib.util.spec_from_file_location(
    "migration_076_session_journals_succession_fields", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_076 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_076)


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
            summary TEXT NOT NULL
        );
        """
    )
    return c


def test_migration_adds_succession_columns(conn: sqlite3.Connection) -> None:
    migration_076.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    for name in ("closed_by", "sealed_by", "sealed_on", "conversation_uuid"):
        assert name in cols
