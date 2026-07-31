"""Tests for endeavor birth gate (todo:endeavor-birth-gate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex_store.db import json_encode, query
from cortex_store.dispatch_ops import execute_op
from cortex_store.endeavor_birth import (
    apply_5129_repair,
    check_endeavor_birth_incomplete,
    detect_endeavor_birth_audit,
    detect_endeavor_cowork_project_stale,
    dispose_row,
    lock_ready,
    undisposed_count,
    write_row,
)
from cortex_store.endeavor_birth.strategy_row import validate_disposition


@pytest.fixture()
def bound_cortex(migrated_conn, monkeypatch: pytest.MonkeyPatch):
    from cortex_store import db

    db_path = Path(migrated_conn.execute("PRAGMA database_list").fetchone()[2])
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    return migrated_conn


def _insert_host(conn, host_id: str, attrs: dict) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, attributes, created_at, updated_at) "
        "VALUES (?, 'opportunity', ?, ?, datetime('now'), datetime('now'))",
        (host_id, host_id, json_encode(attrs)),
    )
    conn.commit()


def test_birth_warning_fires_without_suppressor(bound_cortex) -> None:
    _insert_host(
        bound_cortex,
        "opportunity:test-endeavor-host",
        {"mode": "endeavor"},
    )
    warning = check_endeavor_birth_incomplete(
        bound_cortex,
        entity_id="opportunity:test-endeavor-host",
        entity_type="opportunity",
        attrs={"mode": "endeavor"},
    )
    assert warning is not None
    assert "ring_thread" in warning["missing"]
    assert warning["resume_blocking"] is True


def test_birth_warning_suppressed_by_ack(bound_cortex) -> None:
    warning = check_endeavor_birth_incomplete(
        bound_cortex,
        entity_id="opportunity:acked-host",
        entity_type="opportunity",
        attrs={"mode": "endeavor", "endeavor_birth_ack": "documented"},
    )
    assert warning is None


def test_pending_not_fourth_disposition() -> None:
    with pytest.raises(ValueError, match="pending is not a disposition"):
        validate_disposition("pending")


def test_write_row_pin_null_falsifier(bound_cortex) -> None:
    host = "opportunity:uri-complete-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "5129",
            "endeavor_charter_uri": "cortex://notes/system/threads/5129-endeavor-charter.md",
        },
    )
    result = write_row(
        host,
        {
            "row_id": "r1",
            "material": True,
            "disposition": None,
            "reason": "needs pin",
            "affects": ["deliverable:d1"],
            "authority": "agent:test",
        },
    )
    assert result["assertion_id"] > 0
    assert result["pin"] == result["assertion_id"]
    ready, blocking = lock_ready(bound_cortex, host, "deliverable:d1")
    assert ready is False
    assert blocking
    assert undisposed_count(bound_cortex, host) == 1


def test_lock_ready_deliverable_scoped(bound_cortex) -> None:
    host = "opportunity:scoped-lock-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "1",
            "endeavor_charter_uri": "cortex://x",
        },
    )
    write_row(
        host,
        {
            "row_id": "r-d2",
            "material": True,
            "disposition": None,
            "reason": "pending d2 only",
            "affects": ["deliverable:d2"],
            "authority": "agent:test",
        },
    )
    ready_d1, _ = lock_ready(bound_cortex, host, "deliverable:d1")
    ready_d2, blocking_d2 = lock_ready(bound_cortex, host, "deliverable:d2")
    assert ready_d1 is True
    assert ready_d2 is False
    assert blocking_d2


def test_dispose_clears_lock_ready(bound_cortex) -> None:
    host = "opportunity:dispose-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "1",
            "endeavor_charter_uri": "cortex://x",
        },
    )
    write_row(
        host,
        {
            "row_id": "r1",
            "material": True,
            "disposition": None,
            "reason": "pending",
            "affects": ["deliverable:d1"],
            "authority": "agent:test",
        },
    )
    ready_before, _ = lock_ready(bound_cortex, host, "deliverable:d1")
    assert ready_before is False
    dispose_row(host, "r1", "express", authority="agent:test")
    ready_after, blocking_after = lock_ready(bound_cortex, host, "deliverable:d1")
    assert ready_after is True
    assert blocking_after == []


def test_t1_audit_acknowledged_unrepaired(bound_cortex) -> None:
    host = "opportunity:audit-host"
    _insert_host(
        bound_cortex,
        host,
        {"mode": "endeavor", "endeavor_birth_ack": "known gap"},
    )
    findings = detect_endeavor_birth_audit(bound_cortex, subject=host)
    assert findings
    detail = json.loads(findings[0]["detail"])
    assert detail["ack_state"] == "acknowledged-unrepaired"
    assert detail["resume_blocking"] is True


def test_5129_repair_idempotent(bound_cortex) -> None:
    host = "opportunity:scc-pharmacist-outpatient-26R27D"
    _insert_host(
        bound_cortex,
        host,
        {"mode": "endeavor", "bus_thread": "5129"},
    )
    first = apply_5129_repair(bound_cortex)
    bound_cortex.commit()
    assert first["applied"] is True
    assert first["repaired"] >= 1
    second = apply_5129_repair(bound_cortex)
    assert second["repaired"] == 0
    assert second["residual"] == 0
    assert second["applied"] is False
    rows = query(
        bound_cortex,
        "SELECT attributes FROM entities WHERE id = ?",
        (host,),
    )
    attrs = json.loads(rows[0]["attributes"])
    assert attrs["ring_thread"] == "5129"
    assert "bus_thread" not in attrs
    assert attrs["endeavor_charter_uri"]
    assert attrs["endeavor_scoreboard_uri"]


def test_dispatch_write_row_op(bound_cortex) -> None:
    host = "opportunity:dispatch-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "9",
            "endeavor_charter_uri": "cortex://x",
        },
    )
    result = execute_op(
        "endeavor_write_row",
        {
            "host": host,
            "fields": {
                "row_id": "dispatch-r1",
                "material": True,
                "disposition": None,
                "reason": "via dispatch",
                "affects": ["deliverable:d1"],
                "authority": "agent:test",
            },
        },
    )
    assert "error" not in result
    assert result["lock_ready"] is False


def test_cowork_project_absent_never_blocks_birth(bound_cortex) -> None:
    """Birth-hook absorb: missing cowork_project is not a gate finding."""
    host = "opportunity:no-chrome-host"
    attrs = {
        "mode": "endeavor",
        "ring_thread": "1",
        "endeavor_charter_uri": "cortex://charter",
    }
    _insert_host(bound_cortex, host, attrs)
    warning = check_endeavor_birth_incomplete(
        bound_cortex,
        entity_id=host,
        entity_type="opportunity",
        attrs=attrs,
    )
    assert warning is None
    assert detect_endeavor_cowork_project_stale(bound_cortex, subject=host) == []


def test_cowork_project_stale_t3(bound_cortex) -> None:
    host = "opportunity:stale-chrome-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "1",
            "endeavor_charter_uri": "cortex://charter",
            "cowork_project": "not-a-uuid",
        },
    )
    findings = detect_endeavor_cowork_project_stale(bound_cortex, subject=host)
    assert len(findings) == 1
    detail = json.loads(findings[0]["detail"])
    assert detail["tier"] == "T3"
    assert detail["resume_blocking"] is False
    assert detail["stale_pointer"] == "cowork_project"
    assert detail["stale_reason"] == "invalid_uuid"


def test_cowork_project_valid_uuid_no_t3(bound_cortex) -> None:
    host = "opportunity:valid-chrome-host"
    _insert_host(
        bound_cortex,
        host,
        {
            "mode": "endeavor",
            "ring_thread": "1",
            "endeavor_charter_uri": "cortex://charter",
            "cowork_project": "019f68e7-390f-74b5-af7d-9564b56e13e8",
        },
    )
    assert detect_endeavor_cowork_project_stale(bound_cortex, subject=host) == []
    warning = check_endeavor_birth_incomplete(
        bound_cortex,
        entity_id=host,
        entity_type="opportunity",
        attrs={
            "mode": "endeavor",
            "ring_thread": "1",
            "endeavor_charter_uri": "cortex://charter",
            "cowork_project": "019f68e7-390f-74b5-af7d-9564b56e13e8",
        },
    )
    assert warning is None
