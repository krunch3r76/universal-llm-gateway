"""D1 planned-create resolution and D2 bare-ref candidate surfacing."""

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
            ("matter:estate-2024", "matter", "Estate 2024"),
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


def test_d1_planned_create_resolves_later_statement(
    seeded_conn: sqlite3.Connection,
) -> None:
    """Earlier entity_create id resolves for a later literal statement (p01)."""
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "todo:renew-passport", "@type": "todo", "name": "Renew passport"},
            {"@id": "todo:renew-passport", "priority": "high"},
        ],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert candidates == []
    ops = [entry["op"] for entry in plan]
    assert ops == ["entity_create", "entity_update"]
    assert plan[1]["args"]["attributes"] == {"priority": "high"}


def test_d1_multi_statement_create_and_relationship(
    seeded_conn: sqlite3.Connection,
) -> None:
    """Planned create resolves for a later relationship statement (p20)."""
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "todo:follow-up", "@type": "todo", "name": "Follow up"},
            {"@id": "todo:follow-up", "child_of": {"@id": "matter:estate-2024"}},
        ],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert candidates == []
    ops = [entry["op"] for entry in plan]
    assert ops == ["entity_create", "relationship_create"]


def test_d2_bare_alias_yields_candidates_not_resolve(
    seeded_conn: sqlite3.Connection,
) -> None:
    """Bare refs without ':' surface candidates even when alias is unique (p17)."""
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [{"@id": "Alice", "noted": "call back Tuesday"}],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert plan == []
    assert len(candidates) == 1
    assert candidates[0]["input_ref"] == "Alice"
    assert candidates[0]["matches"]


def test_ac4_dup_typed_id_emits_two_entity_creates() -> None:
    """Duplicate typed @id — same entity_create count as base (AC4-dup)."""
    reg = load_registry()

    class _NoopConn:
        def execute(self, *args: object) -> None:
            return None

        def commit(self) -> None:
            return None

    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {"@id": "document:dup-proof", "@type": "document", "name": "Alpha"},
            {"@id": "document:dup-proof", "@type": "document", "name": "Beta"},
        ],
    }
    plan, candidates = build_op_plan(patch, reg, _NoopConn())  # type: ignore[arg-type]
    assert candidates == []
    creates = [entry for entry in plan if entry["op"] == "entity_create"]
    assert len(creates) == 2
    assert creates[0]["args"]["id"] == creates[1]["args"]["id"] == "document:dup-proof"


def test_d1_forward_ref_planned_ids_prescan_and_ordering(
    seeded_conn: sqlite3.Connection,
) -> None:
    """entity_create ops precede relationship even when typed statements are later."""
    reg = load_registry()
    patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {
                "@id": "person:x",
                "correspondence_with": {"@id": "document:letter-1"},
            },
            {"@id": "document:letter-1", "@type": "document", "name": "Letter"},
            {"@id": "person:x", "@type": "person", "name": "Pat"},
        ],
    }
    plan, candidates = build_op_plan(patch, reg, seeded_conn)
    assert candidates == []
    ops = [entry["op"] for entry in plan]
    assert ops == ["entity_create", "entity_create", "relationship_create"]
