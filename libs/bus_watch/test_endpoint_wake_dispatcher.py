"""Dispatcher allowlist, debounce, retry, lifespan, and outcome table."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.server import create_app
from background_supervisor import supervise

from bus_watch.endpoint_wake_dispatcher import (
    classify_cse,
    classify_display,
    fire_due_wakes,
    qualifies,
    scan_once,
)


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_cse_allowlist_not_display(db: None) -> None:
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "CONSULT|x"}, cse=True)
    assert not qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "CONSULT_REPLY|x"}, cse=True)
    assert not qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "MEMO|x"}, cse=True)
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "TYPE: DIRECTIVE — go"}, cse=True)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "MEMO|x"}, cse=False)
    assert not qualifies({"to_agent": "all", "from_agent": "cursor", "subject": "DONE|x"}, cse=False)
    assert not qualifies({"to_agent": "cursor", "from_agent": "cursor", "subject": "DONE|x"}, cse=False)


def test_retry_pending_and_paste_failed_unknown(db: None) -> None:
    detail = create_thread(thread_id=None, slug="retry")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|x", body="b"
    )
    item = {"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": turn_id, "turn_number": number}
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan("endpoint-wake-dispatcher", 0, turn_id, [item], now="2020-01-01 00:00:00")
    wake = claim_due_wakes(now="2020-01-01 00:00:01")["wake_id"]
    record_wake_result(wake, "pending", "active_chat_busy", "exact", 1, None, now="2020-01-01 00:00:02")
    assert claim_due_wakes(now="2020-01-01 00:00:30")["wake_id"] is None
    again = claim_due_wakes(now="2020-01-01 00:01:03")
    assert again["wake_id"]
    record_wake_result(again["wake_id"], "unknown", "paste_failed", "exact", 1, None, now="2020-01-01 00:01:04")
    assert claim_due_wakes(now="2020-01-01 00:20:00")["wake_id"] is None


def test_debounce_second_turn_waits(db: None) -> None:
    detail = create_thread(thread_id=None, slug="deb")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _t, a_n, _c = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|a", body="a"
    )
    b_id, _t2, b_n, _c2 = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|b", body="b"
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher", 0, a_id,
        [{"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": a_id, "turn_number": a_n}],
        now="2020-01-01 00:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")
    record_wake_result(wake["wake_id"], "delivered", "ok", "exact", 1, None, now="2020-01-01 00:00:02")
    advance_scan(
        "endpoint-wake-dispatcher", a_id, b_id,
        [{"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": b_id, "turn_number": b_n}],
        now="2020-01-01 00:00:30",
    )
    assert claim_due_wakes(now="2020-01-01 00:00:40")["wake_id"] is None
    later = claim_due_wakes(now="2020-01-01 00:10:03")
    assert [r["turn_id"] for r in later["rows"]] == [b_id]


def test_outcome_table() -> None:
    assert classify_display(200, {"ok": True, "delivered": True}, None)[0] == "delivered"
    assert classify_display(200, {"ok": False, "pasted": True, "delivered": False}, None)[0] == "unknown"
    assert classify_display(200, {"ok": False, "reason": "paste_failed", "pasted": False}, None)[0] == "unknown"
    assert classify_display(200, {"ok": False, "reason": "active_chat_busy", "pasted": False}, None)[0] == "pending"
    assert classify_display(500, {"ok": False}, None)[0] == "unknown"
    err = httpx.ConnectError("down")
    assert classify_display(0, None, err)[0] == "pending"
    assert classify_cse({"ok": False, "error": "cdp-ask unreachable: x"})[0] == "unknown"
    assert classify_cse({"ok": False})[0] == "unknown"
    assert classify_cse({"ok": False, "error": "cdp-ask HTTP 502", "status_code": 502})[0] == "refused"


def test_fire_holders_exception_records_unknown_and_does_not_propagate(db: None) -> None:
    detail = create_thread(thread_id=None, slug="holders-fire")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="web-anthropic",
        subject="CONSULT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "web-anthropic",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:01")
    assert claimed["wake_id"]

    class Store:
        def claim(self) -> dict:
            return claimed

        def holder(self, lane_thread_id: str) -> dict:
            raise httpx.ConnectError("giw holders down")

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    returned = fire_due_wakes(Store())  # type: ignore[arg-type]
    assert returned["wake_id"] == claimed["wake_id"]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE wake_id=?",
            (claimed["wake_id"],),
        ).fetchone()
    assert row["state"] == "unknown"
    assert row["reason"] == "ConnectError"


def test_scan_holders_exception_propagates() -> None:
    class Boom:
        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 1,
                    "to_agent": "web-anthropic",
                    "from_agent": "cursor",
                    "thread": "t",
                    "turn_number": 1,
                    "subject": "CONSULT|x",
                }
            ]

        def holder(self, thread: str) -> dict:
            raise httpx.ConnectError("giw holders down")

    with pytest.raises(httpx.ConnectError):
        scan_once(Boom())  # type: ignore[arg-type]


def test_scan_store_connect_error_propagates() -> None:
    class Boom:
        def cursor(self) -> dict:
            raise httpx.ConnectError("store down")

    with pytest.raises(httpx.ConnectError):
        scan_once(Boom())  # type: ignore[arg-type]


def test_lifespan_gate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()

    async def _off() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            names = {t.get_name() for t in asyncio.all_tasks()}
            assert "endpoint_wake_dispatcher" not in names

    asyncio.run(_off())
    monkeypatch.setenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", "1")

    async def _on() -> None:
        app = create_app()
        async with app.router.lifespan_context(app):
            names = {t.get_name() for t in asyncio.all_tasks()}
            assert "endpoint_wake_dispatcher" in names

    asyncio.run(_on())


def test_supervise_respawns_after_connect_error() -> None:
    class App:
        def __init__(self) -> None:
            self.state = type("S", (), {"shutting_down": False})()

    calls = {"n": 0}

    async def loop() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("store down")
        await asyncio.sleep(30)

    async def run() -> None:
        supervise(App(), "endpoint_wake_dispatcher", loop, restart=True, max_restarts=None)
        await asyncio.sleep(1.2)
        assert calls["n"] >= 2

    asyncio.run(run())


