"""Unit tests for migration 065 (derived_from structural relationship type)."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_MIG_PATH = (
    Path(__file__).parent.parent
    / "migrations"
    / "065_derived_from_relationship_type.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_065_derived_from_relationship_type", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_065 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_065)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE relationship_types (
            type TEXT PRIMARY KEY,
            description TEXT
        )
        """
    )
    return c


def test_derived_from_seeded(conn: sqlite3.Connection) -> None:
    migration_065.migrate(conn)
    row = conn.execute(
        "SELECT description FROM relationship_types WHERE type = 'derived_from'"
    ).fetchone()
    assert row is not None
    assert "Derived-view provenance" in row["description"]


def test_idempotent_rerun(conn: sqlite3.Connection) -> None:
    migration_065.migrate(conn)
    migration_065.migrate(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM relationship_types WHERE type = 'derived_from'"
    ).fetchone()["n"]
    assert count == 1
