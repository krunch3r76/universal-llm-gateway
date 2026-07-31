"""Op-plan golden cases and ambiguity → candidates."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.life_imprint.op_plan import build_op_plan
from cortex_store.life_imprint.registry import load_registry


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        [
            ("person:alice", "person", "Alice"),
            ("person:bob", "person", "Bob"),
            ("todo:ship", "todo", "Ship feature"),
            ("account:chk", "account", "Checking"),
            ("organization:acme", "organization", "Acme"),
        ],
    )
    conn.executemany(
        "INSERT INTO entity_aliases (entity_id, entity_type, alias) VALUES (?, ?, ?)",
        [
            ("person:alice", "person", "Alice"),
            ("person:bob", "person", "Bob"),
        ],
    )
    conn.commit()


@pytest.fixture()
def seeded_conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed(migrated_conn)
    return migrated_conn


def test_op_plan_entity_create(seeded_conn: sqlite3.Connection) -> None:
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:new-item", "@type": "todo", "name": "New"}],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert candidates == []
    assert len(plan) == 1
    assert plan[0]["op"] == "entity_create"
    assert plan[0]["args"]["type"] == "todo"


def test_op_plan_relationship(seeded_conn: sqlite3.Connection) -> None:
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {
                "@id": "person:alice",
                "sibling_of": {"@id": "person:bob"},
            }
        ],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert candidates == []
    assert plan[0]["op"] == "relationship_create"
    assert plan[0]["args"]["type_id"] == "sibling_of"


def test_op_plan_assert_noted(seeded_conn: sqlite3.Connection) -> None:
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "todo:ship", "noted": "Blocked on review"}],
    }
    plan, _ = build_op_plan(patch, reg, seeded_conn)
    assert plan[0]["op"] == "assert"
    assert plan[0]["args"]["confidence"] == "believed"


def test_op_plan_due_and_priority(seeded_conn: sqlite3.Connection) -> None:
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "todo:ship", "due": "2026-08-15"},
            {"@id": "todo:ship", "priority": "high"},
        ],
    }
    plan, _ = build_op_plan(patch, reg, seeded_conn)
    attrs = [entry["args"]["attributes"] for entry in plan if entry["op"] == "entity_update"]
    assert {"deadline_date": "2026-08-15"} in attrs
    assert {"priority": "high"} in attrs


def test_ambiguous_surface_form_yields_candidates(seeded_conn: sqlite3.Connection) -> None:
    conn = seeded_conn
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES ('person:carol', 'person', 'Carol')"
    )
    conn.executemany(
        "INSERT INTO surface_forms (mention, entity_id, context_hash) VALUES (?, ?, ?)",
        [
            ("Carol", "person:alice", "hash1"),
            ("Carol", "person:carol", "hash2"),
        ],
    )
    conn.commit()

    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "Carol", "noted": "call back"}],
    }
    plan, candidates = build_op_plan(patch, reg, conn)
    assert plan == []
    assert candidates and candidates[0]["matches"]
