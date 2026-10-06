from __future__ import annotations

import time

from claude_bundles.cdp_registry import execution_state as es


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
