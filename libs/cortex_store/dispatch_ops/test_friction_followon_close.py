"""Auto friction_close when a spawned follow-on todo hits workflow_state=done."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.dispatch_ops._friction_followon_close import (
    close_spawned_friction_on_todo_done,
)
from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_get
from cortex_store.dispatch_ops.test_friction_to_todo import (
    _FRICTION_CLAIM,
    _SKILL_ENTITY,
    _insert_friction,
    _patch_supersede_side_effects,
    _seed_skill_entity,
)


def _seed_followon_todo(conn, *, todo_id: str, friction_id: int) -> None:  # type: ignore[no-untyped-def]
    attrs = json.dumps(
        {
            "spawned_by_friction": friction_id,
            "dispatch_lane": "path-sim-admit-gate",
            "followon_of": "5854",
        }
    )
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state, attributes) "
        "VALUES (?, 'todo', ?, 'open', ?)",
        (todo_id, f"followon {friction_id}", attrs),
    )
    conn.commit()


@pytest.fixture()
def followon_db(
    migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, str]:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    _patch_supersede_side_effects(monkeypatch)
    todo_id = "todo:friction-followon-close-sample"
    with cortex_db.cortex_conn() as conn:
        _seed_skill_entity(conn)
        friction_id = _insert_friction(conn, _FRICTION_CLAIM)
        _seed_followon_todo(conn, todo_id=todo_id, friction_id=friction_id)
    return friction_id, todo_id


@pytest.mark.offline
def test_todo_done_closes_spawned_friction(followon_db: tuple[int, str]) -> None:
    friction_id, todo_id = followon_db
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        result = close_spawned_friction_on_todo_done(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            new_workflow_state="done",
            prior_workflow_state="open",
        )
    assert result is not None
    assert result.get("status") == "closed"
    closed = _op_assertion_get(assertion_id=friction_id)
    assert closed.get("superseded_by") is not None
    assert closed.get("valid_until") is not None


@pytest.mark.offline
def test_todo_done_idempotent_when_already_closed(
    followon_db: tuple[int, str],
) -> None:
    friction_id, todo_id = followon_db
    from cortex_store import db as cortex_db

    with cortex_db.cortex_conn() as conn:
        first = close_spawned_friction_on_todo_done(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            new_workflow_state="done",
            prior_workflow_state="open",
        )
        second = close_spawned_friction_on_todo_done(
            conn,
            entity_id=todo_id,
            entity_type="todo",
            new_workflow_state="done",
            prior_workflow_state="in_progress",
        )
    assert first is not None and first.get("status") == "closed"
    assert second is not None and second.get("status") == "already_closed"
    assert second.get("fulfillment_assertion_id") == first.get(
        "fulfillment_assertion_id"
    )
    _ = friction_id


@pytest.mark.offline
def test_no_spawned_attr_is_noop(
    migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT INTO entities (id, type, name, workflow_state) "
            "VALUES ('todo:plain', 'todo', 'plain', 'open')"
        )
        conn.commit()
        assert (
            close_spawned_friction_on_todo_done(
                conn,
                entity_id="todo:plain",
                entity_type="todo",
                new_workflow_state="done",
                prior_workflow_state="open",
            )
            is None
        )


@pytest.mark.offline
def test_seed_refuses_second_slug_for_same_friction(
    followon_db: tuple[int, str],
) -> None:
    friction_id, todo_id = followon_db
    from cortex_store.dispatch_ops._recon_seed import seed_recon_todo

    result = seed_recon_todo(
        todo_id=f"todo:friction-{friction_id}-duplicate-slug",
        name="dup",
        source_uri="cortex://notes/system/specs/dup.md",
        required_skills=[],
        seed_ack="should refuse",
        context_target_id=_SKILL_ENTITY,
        extra_attrs={"spawned_by_friction": friction_id},
    )
    assert isinstance(result, dict) and "error" in result
    assert todo_id in result["error"]
