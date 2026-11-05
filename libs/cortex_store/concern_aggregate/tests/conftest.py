"""Fixtures for concern aggregate acceptance tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cortex_store._test_db_bootstrap import fresh_migrated_connection
from cortex_store.concern_aggregate.fault_injection import clear_all


@pytest.fixture(autouse=True)
def _clear_fault_hooks() -> None:
    clear_all()
    yield
    clear_all()


@pytest.fixture
def concern_db(
    tmp_path: Path,
    migrated_db_template: Path,
) -> sqlite3.Connection:
    conn = fresh_migrated_connection(tmp_path, migrated_db_template)
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        ("decision:test-auth", "decision", "test authority"),
    )
    conn.commit()
    return conn


def seed_authority(conn: sqlite3.Connection, entity_id: str = "decision:test-auth") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "decision", "test authority"),
    )
    conn.commit()
    return entity_id
