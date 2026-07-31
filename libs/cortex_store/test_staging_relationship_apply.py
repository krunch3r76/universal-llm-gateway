"""Hermetic tests for staging relationship/add apply path."""

from __future__ import annotations

import pytest

from cortex_store.conftest import bind_cortex_db
from cortex_store.db import json_encode, query
from cortex_store.routes.staging import _apply_proposal


def _seed_relationship_type(conn, rel_type: str = "deadline_for") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        (rel_type, "test relationship"),
    )


def _seed_entities(conn, *entity_ids: str) -> None:
    now = "2026-07-14T00:00:00Z"
    for eid in entity_ids:
        conn.execute(
            "INSERT INTO entities (id, type, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (eid, "matter", eid, now, now),
        )


@pytest.mark.offline
def test_apply_proposal_relationship_add_creates_edge(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        _seed_relationship_type(conn)
        _seed_entities(conn, "deadline:test-1", "matter:test-matter")
        proposal = {
            "proposal_type": "relationship",
            "proposal_action": "add",
            "proposal_json": json_encode(
                {
                    "source_id": "deadline:test-1",
                    "target_id": "matter:test-matter",
                    "type_id": "deadline_for",
                    "role": "payment",
                    "evidence": "digest test",
                }
            ),
            "chunk_id": None,
        }
        resolved = _apply_proposal(conn, proposal)
        conn.commit()

        rows = query(
            conn,
            "SELECT id, type, from_entity, to_entity, role "
            "FROM relationships WHERE id = ?",
            (int(resolved),),
        )

    assert rows
    row = rows[0]
    assert row["type"] == "deadline_for"
    assert row["from_entity"] == "deadline:test-1"
    assert row["to_entity"] == "matter:test-matter"
    assert row["role"] == "payment"
