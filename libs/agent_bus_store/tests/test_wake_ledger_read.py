"""GET /endpoint-wake/ledger — read-only wake ledger for agent_bus_read."""

from __future__ import annotations

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import advance_scan, get_cursor
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


@pytest.mark.offline
def test_wake_ledger_route_filters_and_maps_rule_id(client: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="wake-ledger-read")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, turn_number, _c = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="grok-bot-gc1",
        subject="CLOSEOUT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        int(turn_id),
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": int(turn_id),
                "turn_number": int(turn_number),
                "route": "exact",
                "decided_by_rule": "wake_gate_or_directive",
            }
        ],
    )
    resp = client.get(
        "/endpoint-wake/ledger",
        params={"thread": thread, "turn_id": turn_id, "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    row = body["rows"][0]
    assert row["thread_id"] == thread
    assert row["turn_id"] == int(turn_id)
    assert row["state"] == "pending"
    assert row["rule_id"] == "wake_gate_or_directive"
    assert "decided_by_rule" not in row
