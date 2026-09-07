"""Migration 077 unit tests — verbatim fingerprint columns on session_journals."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent.parent
    / "migrations"
    / "077_session_journals_verbatim_fingerprint.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_077_session_journals_verbatim_fingerprint", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_077 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_077)


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
            closed_by TEXT,
            sealed_by TEXT,
            sealed_on TEXT,
            conversation_uuid TEXT
        );
        """
    )
    return c


def test_migration_adds_verbatim_columns(conn: sqlite3.Connection) -> None:
    migration_077.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    assert "verbatim_sha256" in cols
    assert "verbatim_bytes" in cols


def test_migration_is_idempotent(conn: sqlite3.Connection) -> None:
    migration_077.migrate(conn)
    migration_077.migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")}
    assert "verbatim_sha256" in cols
    assert "verbatim_bytes" in cols
