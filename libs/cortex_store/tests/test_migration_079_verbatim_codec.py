"""Migration 079: verbatim_codec column + md-v1 backfill."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent.parent / "migrations" / "079_session_journals_verbatim_codec.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_079_session_journals_verbatim_codec", _MIG_PATH
)
migration_079 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_079)

pytestmark = pytest.mark.offline


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE session_journals (id INTEGER PRIMARY KEY, session_id TEXT)"
    )
    c.execute(
        "INSERT INTO session_journals (session_id) VALUES ('cursor-2026-01-01-000000-abc')"
    )
    return c


def test_migration_adds_verbatim_codec_and_backfills(conn: sqlite3.Connection) -> None:
    migration_079.migrate(conn)
    row = conn.execute(
        "SELECT verbatim_codec FROM session_journals WHERE session_id = ?",
        ("cursor-2026-01-01-000000-abc",),
    ).fetchone()
    assert row is not None
    assert row[0] == "md-v1"
    nulls = conn.execute(
        "SELECT COUNT(*) FROM session_journals WHERE verbatim_codec IS NULL"
    ).fetchone()[0]
    assert nulls == 0
