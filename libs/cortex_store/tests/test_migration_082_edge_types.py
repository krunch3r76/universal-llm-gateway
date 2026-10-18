"""Migration 082 — `same_class_as` and `duplicates` session_edge_types (a:39610)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.mark.offline
def test_migration_082_registers_same_class_as_and_duplicates(migrated_db_path: Path) -> None:
    conn = sqlite3.connect(str(migrated_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT type, description, directional FROM session_edge_types "
            "WHERE type IN ('same_class_as', 'duplicates') ORDER BY type"
        ).fetchall()
    finally:
        conn.close()

    by_type = {row["type"]: dict(row) for row in rows}
    assert set(by_type) == {"duplicates", "same_class_as"}
    assert bool(by_type["same_class_as"]["directional"]) is False
    assert bool(by_type["duplicates"]["directional"]) is True
