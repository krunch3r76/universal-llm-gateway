"""v1.3.1 migration 039 idempotence and column presence tests."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent / "migrations" / "039_normalization_decision_ledger.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_039_normalization_decision_ledger", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_039 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_039)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            predicate_form TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return c


def test_039_migration_adds_columns_and_indices(conn: sqlite3.Connection) -> None:
    # First run
    migration_039.migrate(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(assertions)").fetchall()}
    assert "raw_predicate_form" in cols
    assert "normalization_decision" in cols
    assert "candidate_set_fingerprint" in cols
    assert "normalizer_version" in cols

    # Indices exist
    idx_names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_assertions_%'"
        ).fetchall()
    }
    assert "idx_assertions_normalization_decision" in idx_names
    assert "idx_assertions_raw_predicate_form" in idx_names

    # Re-run is no-op (idempotent)
    migration_039.migrate(conn)
    # Still has them
    cols2 = {row[1] for row in conn.execute("PRAGMA table_info(assertions)").fetchall()}
    assert "normalizer_version" in cols2


def test_pre_ledger_rows_remain_null(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence) VALUES ('person:old', 'old claim', 'confirmed')"
    )
    conn.commit()
    migration_039.migrate(conn)
    row = conn.execute(
        "SELECT * FROM assertions WHERE entity_id='person:old'"
    ).fetchone()
    assert row["raw_predicate_form"] is None
    assert row["normalization_decision"] is None
