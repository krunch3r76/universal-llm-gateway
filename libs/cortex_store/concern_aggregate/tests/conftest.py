"""Fixtures for concern aggregate acceptance tests."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from cortex_store._test_db_bootstrap import fresh_migrated_connection
from cortex_store.concern_aggregate.fault_injection import clear_all


@pytest.fixture(autouse=True, scope="session")
def _enable_concern_fault_injection() -> None:
    os.environ["CORTEX_CONCERN_FAULT_INJECTION"] = "1"
    yield


@pytest.fixture(autouse=True)
def _clear_fault_hooks() -> None:
    import cortex_store.concern_aggregate.fault_injection as fi

    fi._enabled = None
    clear_all()
    yield
    fi._enabled = None
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


def append_position_head(
    conn: sqlite3.Connection,
    concern_id: str,
    *,
    record_ref: str,
    expected_revision: int = 0,
    write_intent_id: str = "wi:test-head",
    material: dict | None = None,
    authorized_by: str = "decision:test-auth",
) -> dict:
    from cortex_store.concern_aggregate.journal import append_transition

    return append_transition(
        conn,
        concern_id=concern_id,
        kind="position_head",
        payload={},
        record_ref=record_ref,
        authorized_by=authorized_by,
        expected_revision=expected_revision,
        write_intent_id=write_intent_id,
        material=material if material is not None else {"position": record_ref},
    )


def seed_authority(conn: sqlite3.Connection, entity_id: str = "decision:test-auth") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "decision", "test authority"),
    )
    conn.commit()
    return entity_id
