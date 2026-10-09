"""Migration 081 refuses duplicate session_ids and does not stamp itself."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store._test_db_bootstrap import materialize_head_schema_template
from cortex_store.db import run_migrations

_MIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "081_session_journals_session_id_unique.py"
)
_spec = importlib.util.spec_from_file_location("migration_081", _MIG_PATH)
assert _spec is not None and _spec.loader is not None
migration_081 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_081)

pytestmark = pytest.mark.offline


def _conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "cortex.db"
    materialize_head_schema_template(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _drop_unique(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS idx_session_journals_session_id_unique")
    conn.execute("DELETE FROM schema_version WHERE version = 81")
    conn.commit()


def _insert(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "INSERT INTO session_journals (timestamp, agent, summary, session_id) "
        "VALUES (?, ?, ?, ?)",
        ("2026-01-01T00:00:00Z", "cursor", "row", session_id),
    )


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    return {row[0] for row in rows}


def test_migration_081_duplicates_leave_db_unchanged(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        _drop_unique(conn)
        _insert(conn, "cursor-2026-01-01-000000-aaa")
        _insert(conn, "cursor-2026-01-01-000000-aaa")
        _insert(conn, "cursor-2026-01-01-000000-bbb")
        conn.commit()
        before = conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()[0]
        with pytest.raises(migration_081.Migration081DuplicatesError) as raised:
            migration_081.migrate(conn)
        assert "1 duplicated session_id" in str(raised.value)
        assert "cursor-2026-01-01-000000-aaa" in str(raised.value)
        assert (
            conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()[0] == before
        )
        assert "idx_session_journals_session_id_unique" not in _index_names(conn)
        conn.rollback()
        with pytest.raises(RuntimeError, match="duplicated session_id"):
            run_migrations(conn)
        stamped = conn.execute(
            "SELECT version FROM schema_version WHERE version = 81"
        ).fetchone()
        assert stamped is None
        assert (
            conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()[0] == before
        )
    finally:
        conn.close()


def test_migration_081_clean_creates_index(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    try:
        _drop_unique(conn)
        _insert(conn, "cursor-2026-01-01-000000-ccc")
        conn.commit()
        migration_081.migrate(conn)
        conn.commit()
        assert "idx_session_journals_session_id_unique" in _index_names(conn)
        with pytest.raises(sqlite3.IntegrityError) as raised:
            _insert(conn, "cursor-2026-01-01-000000-ccc")
        assert "session_journals.session_id" in str(raised.value)
    finally:
        conn.close()
