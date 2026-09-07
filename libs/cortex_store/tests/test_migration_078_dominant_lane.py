"""Migration 078 unit tests — dominant_lane column on session_journals."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent.parent
    / "migrations"
    / "078_session_journals_dominant_lane.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_078_session_journals_dominant_lane", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_078 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_078)


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


def test_migration_adds_dominant_lane(conn: sqlite3.Connection) -> None:
    migration_078.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    assert "dominant_lane" in cols


def test_migration_is_idempotent(conn: sqlite3.Connection) -> None:
    migration_078.migrate(conn)
    migration_078.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    assert "dominant_lane" in cols
