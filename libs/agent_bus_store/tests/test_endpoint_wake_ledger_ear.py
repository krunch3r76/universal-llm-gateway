"""EAR wake cooldown bypass tests for claim_due_wakes."""

from __future__ import annotations

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import write_connect
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_claim_due_wakes_releases_ear_inside_cooldown(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="ear-bypass")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _, a_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    w1 = claim_due_wakes(now="2020-01-01 00:00:01")
    record_wake_result(
        w1["wake_id"],
        "delivered",
        "ok",
        "wildcard",
        1,
        None,
        now="2020-01-01 00:00:02",
    )
    ear_id, _, ear_n, _ = insert_turn(
        thread=thread,
        from_agent="impresario-ear",
        to_agent="grok-bot-gc1",
        subject="EAR | root — healthy",
        body="steer",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        ear_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": ear_id,
                "turn_number": ear_n,
            }
        ],
        now="2020-01-01 00:00:10",
    )
    w2 = claim_due_wakes(now="2020-01-01 00:00:20")
    assert w2["wake_id"] is not None


def test_claim_due_wakes_holds_ordinary_inside_cooldown(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="ear-hold")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _, a_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="CHECKPOINT slug",
        body="b",
    )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 10:00:00",
    )
    w1 = claim_due_wakes(now="2020-01-01 10:00:01")
    record_wake_result(
        w1["wake_id"],
        "delivered",
        "ok",
        "wildcard",
        1,
        None,
        now="2020-01-01 10:00:02",
    )
    b_id, _, b_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="CHECKPOINT slug 2",
        body="b2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        b_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": b_id,
                "turn_number": b_n,
            }
        ],
        now="2020-01-01 10:00:10",
    )
    assert claim_due_wakes(now="2020-01-01 10:00:20")["wake_id"] is None


def test_claim_due_wakes_releases_on_agent_id_rotation(db: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="ear-rotate")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _, a_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
        conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, agent_id, state, surface) "
            "VALUES ('grok-bot-gc1', ?, 'tab-a', 'subscribed', 'grok')",
            (thread,),
        )
        sub_a = conn.execute(
            "SELECT id FROM agent_endpoint_subscriptions WHERE agent_id='tab-a'"
        ).fetchone()["id"]
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 14:00:00",
    )
    w1 = claim_due_wakes(now="2020-01-01 14:00:01")
    record_wake_result(
        w1["wake_id"],
        "delivered",
        "ok",
        "exact",
        int(sub_a),
        None,
        now="2020-01-01 14:00:02",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, agent_id, state, surface) "
            "VALUES ('grok-bot-gc1', ?, 'tab-b', 'subscribed', 'grok')",
            (thread,),
        )
    b_id, _, b_n, _ = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REQUEST|y",
        body="b2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        b_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": b_id,
                "turn_number": b_n,
            }
        ],
        now="2020-01-01 14:00:10",
    )
    w2 = claim_due_wakes(now="2020-01-01 14:00:20")
    assert w2["wake_id"] is not None
