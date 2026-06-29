"""Hermetic tests: close_friction_assertion with todo: resolution auto-creates the todo.

Uses the migrated-schema SQLite fixture — no live services required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops._friction_close_impl import close_friction_assertion
from cortex_store.dispatch_ops.ops_entities import _op_entity_get

_SKILL_ENTITY = "agent_skill:test-promoted"
_TODO_ID = "todo:promoted-sample"
_FRICTION_CLAIM = "[tool_error] promoted friction fixture claim"


def _seed_skill_entity(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'agent_skill', ?)",
        (_SKILL_ENTITY, "test-promoted"),
    )
    conn.commit()


def _insert_friction(conn, claim: str = _FRICTION_CLAIM) -> int:  # type: ignore[no-untyped-def]
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, derivation_type)"
        " VALUES (?, ?, 'hypothesized', 'agent_observation')",
        (_SKILL_ENTITY, claim),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _patch_supersede_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress background tasks in _supersede that don't apply in test context."""
    for target in (
        "enrich_background",
        "enrich_old_assertion_events",
        "reindex_assertion_fts",
        "_embed_assertion_background",
    ):
        monkeypatch.setattr(
            f"cortex_store.routes.assertions._supersede.{target}",
            lambda *a, **k: None,
        )

    class _FakeVS:
        @staticmethod
        def is_initialized() -> bool:
            return False

        @staticmethod
        def delete_assertion_embedding(_id: int) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.vector_store", _FakeVS
    )

    class _FakeImpact:
        likely_supersedes: list[int] = []
        touched_assertions: list[object] = []

    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.analyze_assertion_impact",
        lambda *a, **k: _FakeImpact(),
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.dispatch_predicate_extract_background",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._supersede.recompute_entity_substantiation_status",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "cortex_store.dispatch_ops._friction_close_impl.record",
        lambda *a, **k: None,
    )


@pytest.fixture()
def friction_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Seed the test DB, bind cortex_conn, and return the seeded friction assertion id."""
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    _patch_supersede_side_effects(monkeypatch)

    with cortex_db.cortex_conn() as conn:
        _seed_skill_entity(conn)
        return _insert_friction(conn)


@pytest.mark.offline
def test_friction_close_promotion_result(friction_db: int) -> None:
    """close_friction_assertion returns promotion == {"todo_created": todo_id}."""
    result = close_friction_assertion(
        friction_db,
        _TODO_ID,
        agent="t",
        session_id="t",
    )
    assert result.get("status") == "closed"
    assert result.get("promotion") == {"todo_created": _TODO_ID}


@pytest.mark.offline
def test_promoted_todo_attributes(friction_db: int) -> None:
    """Auto-created todo has correct workflow_state, source_uri, and attributes."""
    close_friction_assertion(friction_db, _TODO_ID, agent="t", session_id="t")

    entity = _op_entity_get(entity_id=_TODO_ID)
    assert "error" not in entity, f"todo not found: {entity}"
    assert entity["workflow_state"] == "open"
    assert entity["source_uri"] == "tasks/specs/promoted-sample.md"

    attrs = entity.get("attributes") or {}
    assert "test-promoted" in attrs.get("required_skills", [])
    assert attrs.get("seed_contract_ack")
    assert "density_triage" not in attrs


@pytest.mark.offline
def test_friction_close_todo_dedup(friction_db: int) -> None:
    """Closing a second friction with the same todo: slug returns promotion=None (dedup)."""
    # First close creates the todo
    result1 = close_friction_assertion(friction_db, _TODO_ID, agent="t", session_id="t")
    assert result1.get("promotion") == {"todo_created": _TODO_ID}

    # Seed a second friction on the same entity (cortex_conn already patched)
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        friction2_id = _insert_friction(conn, "second friction for dedup")

    result2 = close_friction_assertion(friction2_id, _TODO_ID, agent="t", session_id="t")
    assert result2.get("status") == "closed"
    # dedup: todo already exists → promotion absent
    assert result2.get("promotion") is None
