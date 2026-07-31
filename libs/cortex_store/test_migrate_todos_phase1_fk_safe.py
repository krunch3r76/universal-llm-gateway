"""Hermetic tests for migrate-todos-to-entities phase1 FK-safe deletes."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.entity_crud import create_entity_impl

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "migrate-todos-to-entities.py"
_spec = importlib.util.spec_from_file_location("migrate_todos_to_entities", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migrate)
phase1_delete_stale_entities = _migrate.phase1_delete_stale_entities


@pytest.mark.offline
def test_phase1_deletes_todo_children_before_entity(
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "todo:fk-safe-phase1-test"
    create_entity_impl(
        migrated_conn,
        {"id": entity_id, "type": "todo", "name": "fk-safe-phase1-test"},
    )
    claim = "phase1 fk-safe claim"
    claim_hash = compute_claim_hash(entity_id, claim)
    migrated_conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, entrenchment_score) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.1)",
        (entity_id, claim, claim_hash),
    )
    migrated_conn.execute(
        "INSERT INTO surface_forms (mention, entity_id, context_hash) VALUES (?, ?, ?)",
        ("fk-safe mention", entity_id, "ctx-hash"),
    )
    migrated_conn.commit()

    counts = phase1_delete_stale_entities(migrated_conn, dry_run=False)
    assert counts["todo_entities"] == 1
    assert counts["assertions"] >= 1
    assert counts["surface_forms"] >= 1

    fk_violations = migrated_conn.execute("PRAGMA foreign_key_check").fetchall()
    assert fk_violations == []
    assert (
        migrated_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.offline
def test_phase1_dry_run_preserved(migrated_conn: sqlite3.Connection) -> None:
    entity_id = "todo:fk-safe-dry-run"
    create_entity_impl(
        migrated_conn,
        {"id": entity_id, "type": "todo", "name": "fk-safe-dry-run"},
    )
    migrated_conn.commit()

    counts = phase1_delete_stale_entities(migrated_conn, dry_run=True)
    assert counts["todo_entities"] == 1
    assert (
        migrated_conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()[0]
        == 1
    )
