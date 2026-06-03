"""Migration 053 + thread_compression backfill helpers (:memory: fixture)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from cortex_store.thread_compression_backfill import (
    boundaries_from_exclusive_upper,
    planned_thread_compression_update,
    run_thread_compression_backfill,
    thread_compression_reasoning_summary,
)

_MIG_PATH = (
    Path(__file__).parent
    / "migrations"
    / "053_thread_compression_derivation_backfill.py"
)
_spec = importlib.util.spec_from_file_location(
    "migration_053_thread_compression_derivation_backfill", _MIG_PATH
)
assert _spec is not None and _spec.loader is not None
migration_053 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_053)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY,
            entity_id TEXT,
            claim TEXT,
            confidence TEXT,
            evidence_uris TEXT,
            derivation_type TEXT,
            predicate_form TEXT,
            reasoning_summary TEXT,
            chunk_id TEXT,
            superseded_by INTEGER
        );
        """
    )
    return c


def _insert_legacy(
    conn: sqlite3.Connection,
    *,
    exclusive_upper: int = 5,
    reasoning_summary: str | None = None,
    chunk_id: str | None = None,
    derivation_type: str = "compression",
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type, "
        "predicate_form, reasoning_summary, chunk_id, evidence_uris) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "thread:anchor-1",
            "archive summary: prior turns collapsed.",
            "confirmed",
            derivation_type,
            f"thread_summary({exclusive_upper})",
            reasoning_summary,
            chunk_id,
            '["workspaces://ulg/.runtime/t/turn_0001.json"]',
        ),
    )
    return int(cur.lastrowid)


def test_boundaries_from_exclusive_upper_matches_handler() -> None:
    assert boundaries_from_exclusive_upper(5) == (4, 5)


def test_planned_update_derives_reasoning_summary() -> None:
    planned = planned_thread_compression_update(
        predicate_form="thread_summary(5)",
        reasoning_summary=None,
        chunk_id=None,
    )
    assert planned is not None
    assert planned["derivation_type"] == "thread_compression"
    parsed = json.loads(planned["reasoning_summary"])
    assert parsed == {
        "covered_through_turn_index": 4,
        "hot_tail_start_turn_index": 5,
    }


def test_planned_clears_chunk_id() -> None:
    planned = planned_thread_compression_update(
        predicate_form="thread_summary(3)",
        reasoning_summary=None,
        chunk_id="abc123-0",
    )
    assert planned is not None
    assert planned["chunk_id"] is None


def test_planned_preserves_existing_valid_boundaries() -> None:
    existing = thread_compression_reasoning_summary(
        covered_through_turn_index=9,
        hot_tail_start_turn_index=10,
    )
    planned = planned_thread_compression_update(
        predicate_form="thread_summary(5)",
        reasoning_summary=existing,
        chunk_id=None,
    )
    assert planned is not None
    assert json.loads(planned["reasoning_summary"]) == {
        "covered_through_turn_index": 9,
        "hot_tail_start_turn_index": 10,
    }


def test_migration_transforms_legacy_row(conn: sqlite3.Connection) -> None:
    row_id = _insert_legacy(conn, exclusive_upper=7)
    migration_053.migrate(conn)
    row = conn.execute(
        "SELECT derivation_type, reasoning_summary, chunk_id, predicate_form "
        "FROM assertions WHERE id = ?",
        (row_id,),
    ).fetchone()
    assert row["derivation_type"] == "thread_compression"
    assert row["chunk_id"] is None
    assert json.loads(row["reasoning_summary"]) == {
        "covered_through_turn_index": 6,
        "hot_tail_start_turn_index": 7,
    }
    assert row["predicate_form"] == "thread_summary(7)"


def test_migration_idempotent(conn: sqlite3.Connection) -> None:
    _insert_legacy(conn)
    migration_053.migrate(conn)
    migration_053.migrate(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM assertions WHERE derivation_type = 'compression'"
    ).fetchone()["n"]
    assert count == 0


def test_migration_skips_document_compression(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type, "
        "chunk_id, evidence_uris) VALUES (?, ?, ?, ?, ?, ?)",
        (
            "legal_source:statute-x",
            "Paraphrase of statute section.",
            "confirmed",
            "compression",
            "hashprefix-0",
            '["https://leginfo.example.gov/statute"]',
        ),
    )
    migration_053.migrate(conn)
    row = conn.execute(
        "SELECT derivation_type, chunk_id FROM assertions WHERE chunk_id IS NOT NULL"
    ).fetchone()
    assert row["derivation_type"] == "compression"
    assert row["chunk_id"] == "hashprefix-0"


def test_run_backfill_dry_run_no_write(conn: sqlite3.Connection) -> None:
    _insert_legacy(conn)
    counts = run_thread_compression_backfill(conn, dry_run=True)
    assert counts.assertions_updated == 1
    row = conn.execute("SELECT derivation_type FROM assertions WHERE id = 1").fetchone()
    assert row["derivation_type"] == "compression"
