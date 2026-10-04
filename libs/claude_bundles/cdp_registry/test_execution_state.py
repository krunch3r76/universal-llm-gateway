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
