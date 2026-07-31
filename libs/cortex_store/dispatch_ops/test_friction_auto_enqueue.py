"""G3 — friction auto-enqueue mint, dedup, sweep idempotence."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex_store.dispatch_ops._friction_enqueue import (
    mint_friction_followon,
    reconcile_charter_frictions,
    repair_todo_slug,
    todo_exists_for_friction,
)
from cortex_store.dispatch_ops.ops_assertions_write import _op_friction
from cortex_store.dispatch_ops.ops_entities import _op_entity_get


def _seed_service(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
        ("service:charter-runner", "charter-runner"),
    )
    conn.commit()


@pytest.fixture()
def enqueue_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        _seed_service(conn)
    result = _op_friction(
        owner="service:charter-runner",
        category="tool_error",
        note=(
            "deploy verify probe failed in "
            "scripts/model_manager/ui/controller/charter_runner/harvest.py"
        ),
        agent="cursor-sdk",
        suggestion="Pass consult provenance kwargs in _evaluate_from_persisted",
        charter_root="5624",
        window_index=4,
        actionable=True,
    )
    assert "error" not in result
    return int((result.get("item") or {})["id"])


@pytest.mark.offline
def test_mint_followon_attrs(enqueue_db: int) -> None:
    row = {
        "id": enqueue_db,
        "entity_id": "service:charter-runner",
        "claim": (
            "[tool_error] deploy verify probe failed in "
            "scripts/model_manager/ui/controller/charter_runner/harvest.py "
            "— Suggestion: Pass consult provenance kwargs"
        ),
        "attributes": {
            "charter_root": "5624",
            "window_index": 4,
            "actionable": True,
            "suggestion": "Pass consult provenance kwargs in _evaluate_from_persisted",
        },
    }
    created = mint_friction_followon(row, root_id="5624")
    assert created == f"todo:friction-{enqueue_db}"
    entity = _op_entity_get(entity_id=created, intent="full")
    assert "error" not in entity
    attrs = entity.get("attributes") or {}
    assert attrs.get("dispatch_lane") == "path-sim-admit-gate"
    assert attrs.get("followon_of") == "5624"
    assert attrs.get("spawned_by_friction") == enqueue_db
    assert attrs.get("detent") == "closed"
    assert attrs.get("check_requested") is False


@pytest.mark.offline
def test_mint_dedup(enqueue_db: int) -> None:
    row = {
        "id": enqueue_db,
        "entity_id": "service:charter-runner",
        "claim": (
            "[tool_error] deploy verify probe failed in "
            "scripts/model_manager/ui/controller/charter_runner/harvest.py"
        ),
        "attributes": {"charter_root": "5624", "actionable": True},
    }
    first = mint_friction_followon(row, root_id="5624")
    assert first == f"todo:friction-{enqueue_db}"
    assert todo_exists_for_friction(enqueue_db) == first
    # Idempotent — returns existing slug, does not remint.
    assert mint_friction_followon(row, root_id="5624") == first


@pytest.mark.offline
def test_skip_non_actionable(enqueue_db: int) -> None:
    row = {
        "id": enqueue_db,
        "entity_id": "service:charter-runner",
        "claim": "[protocol] informational",
        "attributes": {
            "charter_root": "5624",
            "actionable": False,
            "actionable_false_reason": "noise",
        },
    }
    assert mint_friction_followon(row, root_id="5624") is None


@pytest.mark.offline
def test_reconcile_sweep_idempotent(enqueue_db: int) -> None:
    first = reconcile_charter_frictions("5624")
    assert len(first) == 1
    assert first[0]["todo_id"] == f"todo:friction-{enqueue_db}"
    second = reconcile_charter_frictions("5624")
    assert second == []


@pytest.mark.offline
def test_repair_slug_deterministic() -> None:
    assert repair_todo_slug("5624", 7) == "todo:frictions-audit-5624-w7"
    assert repair_todo_slug("agent-bus:5624", 7) == "todo:frictions-audit-5624-w7"
