"""Hermetic tests for assert dry_run preflight."""

from __future__ import annotations

import json

import pytest

from cortex_store.db import query
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert


@pytest.fixture()
def entity_id(cortex_client, migrated_db_path, monkeypatch) -> str:
    from cortex_store import db
    from cortex_store.db import cortex_conn

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    eid = "task:dry-run-preflight-test"
    with cortex_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'task', ?)",
            (eid, "dry-run test"),
        )
        conn.commit()
    return eid


def _count_assertions(entity_id: str, migrated_db_path, monkeypatch) -> int:
    from cortex_store import db
    from cortex_store.db import cortex_conn

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT COUNT(*) AS n FROM assertions WHERE entity_id = ?",
            (entity_id,),
        )
        return int(rows[0]["n"])


def test_dry_run_returns_no_verbatim_without_insert(
    entity_id: str, migrated_db_path, monkeypatch
) -> None:
    before = _count_assertions(entity_id, migrated_db_path, monkeypatch)
    result = _op_assert(
        entity_id=entity_id,
        claim="Confirmed claim with no embedded quote at all here.",
        confidence="confirmed",
        evidence="dry-run test",
        evidence_uris=["agent-bus:4871"],
        derivation_type="agent_observation",
        dry_run=True,
    )
    assert result.get("dry_run") is True
    assert result.get("item") is None
    warnings = result.get("validation_warnings") or []
    auditor = [w for w in warnings if w.get("category") == "auditor"]
    assert auditor
    assert "verbatim" in auditor[0]["message"].lower()
    after = _count_assertions(entity_id, migrated_db_path, monkeypatch)
    assert after == before


def test_dry_run_acknowledge_suppresses_warning(
    entity_id: str, migrated_db_path, monkeypatch
) -> None:
    result = _op_assert(
        entity_id=entity_id,
        claim="Another claim without any quoted span inside.",
        confidence="confirmed",
        evidence="dry-run ack test",
        evidence_uris=["agent-bus:4871"],
        derivation_type="agent_observation",
        acknowledge_audit_gaps=["no_verbatim"],
        dry_run=True,
    )
    warnings = result.get("validation_warnings") or []
    assert not any(w.get("category") == "auditor" for w in warnings)


def test_write_still_persists_without_dry_run(
    entity_id: str, migrated_db_path, monkeypatch
) -> None:
    before = _count_assertions(entity_id, migrated_db_path, monkeypatch)
    result = _op_assert(
        entity_id=entity_id,
        claim="Write path still works for dry_run regression.",
        confidence="believed",
        evidence="write test",
        derivation_type="inference",
        confidence_score=0.8,
        reasoning_summary="Hermetic regression for dry_run default false.",
    )
    assert "error" not in result
    assert result.get("item") is not None
    after = _count_assertions(entity_id, migrated_db_path, monkeypatch)
    assert after == before + 1
