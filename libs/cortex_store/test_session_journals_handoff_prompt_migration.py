"""Migration 044 unit tests — ``session_journals.handoff_prompt`` column add.

Applies the migration to an in-memory SQLite DB pre-seeded with the
pre-044 ``session_journals`` shape and confirms:

  * The ``handoff_prompt`` column is added as nullable TEXT.
  * Re-running the migration is a no-op (no ``OperationalError``).
  * Existing rows present at migration time read back ``handoff_prompt = NULL``.

See ``test_provenance_v1_migration.py`` for the canonical migration-test
pattern used here.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "044_session_journals_handoff_prompt.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_044_session_journals_handoff_prompt", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_044 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_044)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory DB seeded with the pre-044 ``session_journals`` shape."""
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
            prior_session_id TEXT
        );
        """
    )
    return c


def test_migration_adds_handoff_prompt_column(conn: sqlite3.Connection) -> None:
    migration_044.migrate(conn)
    cols = {
        row["name"]: row for row in conn.execute("PRAGMA table_info(session_journals)")
    }
    assert "handoff_prompt" in cols
    handoff_col = cols["handoff_prompt"]
    assert handoff_col["type"] == "TEXT"
    # notnull == 0 means nullable
    assert handoff_col["notnull"] == 0
    # No DEFAULT — column defaults to NULL on existing rows
    assert handoff_col["dflt_value"] is None


def test_migration_is_idempotent(conn: sqlite3.Connection) -> None:
    migration_044.migrate(conn)
    migration_044.migrate(conn)  # second call must not raise
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(session_journals)")]
    # Exactly one handoff_prompt column, not two
    assert cols.count("handoff_prompt") == 1


def test_existing_rows_get_null_handoff_prompt(conn: sqlite3.Connection) -> None:
    """Pre-existing journal rows must read back NULL on the new column."""
    conn.execute(
        "INSERT INTO session_journals "
        "(timestamp, agent, summary, session_id) "
        "VALUES (?, ?, ?, ?)",
        (
            "2026-05-27T00:00:00Z",
            "web",
            "pre-migration row",
            "web-2026-05-27-0000",
        ),
    )
    conn.commit()
    migration_044.migrate(conn)
    row = conn.execute(
        "SELECT handoff_prompt FROM session_journals WHERE session_id = ?",
        ("web-2026-05-27-0000",),
    ).fetchone()
    assert row is not None
    assert row["handoff_prompt"] is None
