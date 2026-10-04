from __future__ import annotations

import pytest

from systems.proxy.routers.api.execution_authority_read import map_authority_to_monitor


def test_map_authority_streaming_to_running() -> None:
    body = map_authority_to_monitor(
        {
            "execution_state": {
                "execution_id": "sg-1",
                "state": "streaming",
                "updated_at": 1_700_000_000.0,
                "holder_pid": 99,
            },
            "execution_state_freshness": "live",
            "as_of": "2026-01-01T00:00:00Z",
        },
        execution_id="sg-1",
    )
    assert body is not None
    assert body["status"] == "running"
    assert body["state"] == "streaming"
    assert body["source"] == "cdp_registry.execution_state"
    assert "recovered_from" not in body.get("recovery", {})


def test_map_store_when_authority_missing() -> None:
    body = map_authority_to_monitor(
        {
            "execution_state": None,
            "store": {"execution_id": "sat-1", "status": "running"},
            "as_of": "2026-01-01T00:00:00Z",
        },
        execution_id="sg-1",
    )
    assert body is not None
    assert body["source"] == "cdp_ask.execution_store"
    assert body["projection_of"] == "cdp_registry.execution_state"
