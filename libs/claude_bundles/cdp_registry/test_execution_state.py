from __future__ import annotations

import contextlib
import time
from typing import Any

import pytest

from claude_bundles.cdp_registry import execution_state as es


@pytest.fixture
def memory_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {
        "reg-1": {"registration_id": "reg-1", "status": "active"},
    }

    @contextlib.contextmanager
    def _ports_lock():
        yield

    monkeypatch.setattr(es._store, "ports_lock", _ports_lock)
    monkeypatch.setattr(es._store, "load_active", lambda: dict(active))

    def _write_active(data: dict[str, dict[str, Any]]) -> None:
        active.clear()
        active.update(data)

    monkeypatch.setattr(es._store, "write_active", _write_active)
    monkeypatch.setattr(es._store, "append_log", lambda *_a, **_k: None)
    return active


def test_execution_state_for_execution_id_ttl_expired() -> None:
    eid = "exec-ttl-test"
    active = {
        "reg-1": {
            "execution_state": {
                "execution_id": eid,
                "state": "streaming",
                "kind": "execution",
                "started_at": time.time() - es.EXECUTION_IN_FLIGHT_TTL_S - 10,
                "updated_at": time.time(),
                "holder_pid": 1,
            }
        }
    }
    hit = es.execution_state_for_execution_id(eid, now=time.time(), active=active)
    assert hit is not None
    _rid, entry, freshness = hit
    assert freshness == "expired_by_ttl"
    assert entry["state"] == "streaming"


def test_followup_in_flight_excluded_from_restart_gate_rows() -> None:
    now = time.time()
    active = {
        "reg-fu": {
            "execution_state": {
                "execution_id": "followup:abc",
                "state": "streaming",
                "kind": "followup",
                "started_at": now - 10,
                "updated_at": now,
                "holder_pid": 1,
            }
        }
    }
    assert "reg-fu" in es.in_flight_rows(active, now=now)
    assert es.in_flight_rows_for_restart_gate(active, now=now) == {}


def test_execution_in_flight_included_in_restart_gate_rows() -> None:
    now = time.time()
    active = {
        "reg-ex": {
            "execution_state": {
                "execution_id": "exec-1",
                "state": "streaming",
                "kind": "execution",
                "started_at": now - 10,
                "updated_at": now,
                "holder_pid": 1,
            }
        }
    }
    assert es.in_flight_rows_for_restart_gate(active, now=now) == active


def test_followup_stamp_does_not_replace_in_flight_execution(
    memory_registry: dict[str, dict[str, Any]],
) -> None:
    """Followup paste must not clobber a live generate/ask execution on the same row."""
    fixed = 1_700_000_000.0
    es.set_execution_state(
        "reg-1",
        execution_id="exec-live",
        state="streaming",
        kind="execution",
        now=fixed,
    )
    es.set_execution_state(
        "reg-1",
        execution_id="followup:abc123",
        state="streaming",
        kind="followup",
        reason="paste:test",
        now=fixed + 1,
    )
    entry = es.execution_state_of(memory_registry["reg-1"])
    assert entry is not None
    assert entry["execution_id"] == "exec-live"
    assert entry["kind"] == "execution"
    gate = es.in_flight_rows_for_restart_gate(memory_registry, now=fixed + 1)
    assert "reg-1" in gate
    assert gate["reg-1"]["execution_state"]["execution_id"] == "exec-live"


def test_row_drain_protection_still_sees_followup_in_flight() -> None:
    from claude_bundles.cdp_registry.dormant_drain import row_drain_protection

    now = time.time()
    row = {
        "execution_state": {
            "execution_id": "followup:abc",
            "state": "streaming",
            "kind": "followup",
            "started_at": now - 10,
            "updated_at": now,
            "holder_pid": 1,
        }
    }
    assert (
        row_drain_protection(row, registration_id="reg-fu", now=now)
        == "execution_in_flight"
    )
