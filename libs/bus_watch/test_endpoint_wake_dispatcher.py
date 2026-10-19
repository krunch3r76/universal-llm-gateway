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
    CSE_WAKE_SUBJECT_HEADS,
    GROK_WAKE_SUBJECT_HEADS,
    classify_cse,
    classify_display,
    fire_due_wakes,
    qualifies,
    scan_once,
    subject_allowed,
)


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_cse_allowlist_not_display(db: None) -> None:
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "CONSULT|x"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-aab1", "subject": "ASK|escrow in opening only?"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-aab1", "subject": "RESULT|escrow-in-opening panel"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "NUDGE|16934#11 ASK"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "web-drive-impresario", "subject": "PLAN r2|web-drive-impresario"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "AMEND #712|Composio mail scope"}, cse=True)
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "CONSULT_REPLY|x"}, cse=True)
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "MEMO|x"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "STATUS|x"}, cse=True)
    assert not qualifies({"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "ACK|x"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "PICKUP|x"}, cse=True)
    assert qualifies({"to_agent": "maestro", "from_agent": "cursor", "subject": "CHECKPOINT|x"}, cse=True)
    assert not qualifies({"to_agent": "maestro", "from_agent": "cursor-sdk", "subject": "CLOSEOUT|x"}, cse=True)
    assert qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk CLOSEOUT a6c1430199ea-a24aece5 contract=freeform",
        },
        cse=False,
    )
    assert not qualifies({"to_agent": "maestro", "from_agent": "cursor-sdk", "subject": "DONE #359 RAG without pipe"}, cse=True)
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "TYPE: DIRECTIVE — go"}, cse=True)
    assert qualifies({"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "FACT|x"}, cse=True)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "MEMO|x"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "FACT|x"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "RULING|x"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-aab1", "from_agent": "grok-bot-gc1", "subject": "RELAY|maestro ACK"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor-sdk", "subject": "RESULT|39359"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-r1", "from_agent": "web-drive-impresario", "subject": "REVIEW-REQUEST|R1"}, cse=False)
    assert subject_allowed("TYPE: ROUTE/CONTROL", cse=False)
    assert not subject_allowed("TYPE: ROUTE/CONTROL", cse=True)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "CHECKPOINT|x"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "STATUS|x"}, cse=False)
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "cursor-sdk generate admitted"},
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-gc2",
            "from_agent": "grok-bot-gc1",
            "subject": "HELLO|gc1->insyncerator|sync + Stage Manager setup",
        },
        cse=False,
    )
    assert CSE_WAKE_SUBJECT_HEADS == {
        "CONSULT",
        "QUESTION",
        "ASK",
        "REQUEST",
        "AMEND",
        "DONE",
        "FAILED",
        "FACT",
        "REVIEW",
        "PLAN",
        "LANDED",
        "VERDICT",
        "RESULT",
        "REPORT",
        "RETURN",
        "NUDGE",
        "SYNTHESIS",
        "CORRECTION",
        "RESTARTED",
        "GATE MISS",
        "ESCALATE",
        "BLOCKED",
        "READY",
    }
    assert GROK_WAKE_SUBJECT_HEADS == {
        "REQUEST",
        "REVIEW-REQUEST",
        "RULING",
        "ACK",
        "AMEND",
        "PARK",
        "CANCEL",
        "WITHDRAW",
        "COMMISSION",
        "DECOMMISSION",
        "WAKE",
        "CONSULT",
        "QUESTION",
        "ASK",
        "DONE",
        "FAILED",
        "FACT",
        "REVIEW",
        "PLAN",
        "LANDED",
        "VERDICT",
        "RESULT",
        "REPORT",
        "RETURN",
        "NUDGE",
        "RELAY",
        "SEND",
        "PASS",
        "SYNTHESIS",
        "CORRECTION",
        "RESTARTED",
        "GATE MISS",
        "ESCALATE",
        "BLOCKED",
        "READY",
    }
    assert not qualifies({"to_agent": "all", "from_agent": "cursor", "subject": "DONE|x"}, cse=False)
    assert not qualifies({"to_agent": "cursor", "from_agent": "cursor", "subject": "DONE|x"}, cse=False)
    # Intermediate impresario telemetry still wakes peer Grok/Maestro seats; cursor-sdk stays filtered.
    for intermediate_subj in (
        "PICKUP|work-1",
        "UPDATE|work-1",
        "HOP|lane-1",
        "FACT|pickup|12797|hop-B",
        "FACT|house-cp|16934|1",
        "FACT|friction:39330|pickup r2 by bl1",
        "FACT|friction:39330|cc pickup #1628",
        "SCORE_RESURFACE — slug G5",
        "park-harvest wake — 17899",
    ):
        assert qualifies(
            {"to_agent": "maestro", "from_agent": "grok-bot-bl1", "subject": intermediate_subj},
            cse=True,
        )
        assert qualifies(
            {"to_agent": "grok-bot-gc1", "from_agent": "grok-bot-bl1", "subject": intermediate_subj},
            cse=False,
        )
        assert not qualifies(
            {"to_agent": "maestro", "from_agent": "cursor-sdk", "subject": intermediate_subj},
            cse=True,
        )
    # ACK/ROUTE receipts to grok-bot-gc1 or maestro do not wake them, while ACK to an impresario house still wakes it
    assert not qualifies({"to_agent": "grok-bot-gc1", "from_agent": "grok-bot-bl1", "subject": "ACK|work-1"}, cse=False)
    assert qualifies({"to_agent": "grok-bot-bl1", "from_agent": "grok-bot-gc1", "subject": "ACK|work-1"}, cse=False)
    assert not qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "conductor-hop",
            "subject": "park-harvest wake — 17934",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "conductor-hop",
            "subject": "park-harvest escalation — 17934",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "conductor-hop",
            "subject": "SCORE_RESURFACE — slug G5",
        },
        cse=True,
    )


def test_dispatch_and_conductor_hop_gate_wakes_route_to_cursor() -> None:
    freeform_closeout = (
        "cursor-sdk CLOSEOUT 940a9f86d053-8e0c8ad5 contract=freeform"
    )
    assert qualifies(
        {
            "to_agent": "dispatch",
            "from_agent": "cursor-sdk",
            "subject": freeform_closeout,
        },
        cse=False,
    )
    assert not qualifies(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": (
                "cursor-sdk CLOSEOUT 2d1f2c04d3ff-15c30a1d "
                "contract=conductor caller=conductor-hop"
            ),
        },
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "conductor-hop",
            "subject": "stop: PARKED_TRANSPORT",
        },
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": "stop: PARKED_TRANSPORT",
        },
        cse=False,
    )

    child_thread = "18273#2"

    class Store:
        def __init__(self) -> None:
            self.pending: list[dict] = []
            self.subscription_calls: list[tuple[str, str]] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 42,
                    "to_agent": "dispatch",
                    "from_agent": "cursor-sdk",
                    "thread": child_thread,
                    "turn_number": 2,
                    "subject": freeform_closeout,
                }
            ]

        def subscription(self, agent: str, thread: str):
            self.subscription_calls.append((agent, thread))
            if agent == "cursor" and thread == child_thread:
                return (
                    "parent",
                    {
                        "id": 3,
                        "surface": "cursor",
                        "display": "1",
                        "title": "house",
                        "agent_id": None,
                    },
                )
            return None

        def scan(self, high: int, new_hw: int, pending: list) -> None:
            self.pending = pending

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert store.subscription_calls == [("cursor", child_thread)]
    assert len(store.pending) == 1
    assert store.pending[0]["agent"] == "cursor"
    assert store.pending[0]["thread_id"] == child_thread


def test_cdp_reply_to_dispatch_on_child_lane_wakes_cursor() -> None:
    parent_thread = "15420"
    child_thread = "15490"

    class Store:
        def __init__(self) -> None:
            self.pending: list[dict] = []
            self.subscription_calls: list[tuple[str, str]] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 99,
                    "to_agent": "dispatch",
                    "from_agent": "web-anthropic",
                    "thread": child_thread,
                    "turn_number": 3,
                    "subject": "cdp reply — 8fec4c00",
                }
            ]

        def subscription(self, agent: str, thread: str):
            self.subscription_calls.append((agent, thread))
            if agent == "cursor" and thread == child_thread:
                return (
                    "parent",
                    {
                        "id": 7,
                        "surface": "cursor",
                        "display": "1",
                        "title": f"{parent_thread} continuity",
                        "agent_id": None,
                    },
                )
            return None

        def scan(self, high: int, new_hw: int, pending: list) -> None:
            self.pending = pending

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert store.subscription_calls == [("cursor", child_thread)]
    assert len(store.pending) == 1
    assert store.pending[0]["agent"] == "cursor"
    assert store.pending[0]["thread_id"] == child_thread
    assert qualifies(
        {
            "to_agent": "dispatch",
            "from_agent": "web-anthropic",
            "subject": "cdp reply — 8fec4c00",
        },
        cse=False,
    )


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


def test_debounce_cleared_after_target_reply(db: None) -> None:
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="deb-reply")
    assert detail is not None
    thread = str(detail["id"])
    # Earlier same-day reply from target and from web-anthropic in production
    # ISO-8601 'T...Z' format must NOT clear a later delivered wake's debounce.
    early_target_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="cursor",
        subject="REPLY|early",
        body="early",
    )
    early_web_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="REPLY|web",
        body="web",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id IN (?, ?)",
            ("2020-01-01T09:00:00Z", early_target_id, early_web_id),
        )
    a_id, _t, a_n, _c = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|a", body="a"
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher", 0, a_id,
        [{"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": a_id, "turn_number": a_n}],
        now="2020-01-01 10:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 10:00:01")
    record_wake_result(wake["wake_id"], "delivered", "ok", "exact", 1, None, now="2020-01-01 10:00:02")
    b_id, _tb, b_n, _cb = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|b", body="b"
    )
    advance_scan(
        "endpoint-wake-dispatcher", a_id, b_id,
        [{"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": b_id, "turn_number": b_n}],
        now="2020-01-01 10:00:10",
    )
    # Before target replies after 10:00:02, debounce holds despite 09:00:00Z turns
    # and despite a post-delivery web-anthropic turn on a non-cse route.
    post_web_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="REPLY|web-after",
        body="web-after",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T10:00:15Z", post_web_id),
        )
    assert claim_due_wakes(now="2020-01-01 10:00:20")["wake_id"] is None

    # Target replies after 10:00:02 in production ISO-8601 'T...Z' format -> debounce clears
    # for subsequent turn c_id posted after the reply.
    reply_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="cursor",
        subject="REPLY|x",
        body="r",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T10:00:25Z", reply_id),
        )
    c_id, _t3, c_n, _c3 = insert_turn(
        thread=thread, from_agent="cursor", to_agent="grok-bot-gc1", subject="FACT|c", body="c"
    )
    advance_scan(
        "endpoint-wake-dispatcher", b_id, c_id,
        [{"agent": "grok-bot-gc1", "thread_id": thread, "turn_id": c_id, "turn_number": c_n}],
        now="2020-01-01 10:00:30",
    )
    early = claim_due_wakes(now="2020-01-01 10:00:40")
    assert early["wake_id"]
    assert [r["turn_id"] for r in early["rows"] if r.get("state") == "claimed"] == [c_id]


def test_cse_route_debounce_cleared_by_web_anthropic_reply(db: None) -> None:
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="deb-cse-reply")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _t, a_n, _c = insert_turn(
        thread=thread, from_agent="cursor", to_agent="maestro", subject="CONSULT|a", body="a"
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher", 0, a_id,
        [{"agent": "maestro", "thread_id": thread, "turn_id": a_id, "turn_number": a_n}],
        now="2020-01-01 10:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 10:00:01")
    record_wake_result(wake["wake_id"], "delivered", "ok", "cse", 9, "reg-1", now="2020-01-01 10:00:02")
    c_id, _t3, c_n, _c3 = insert_turn(
        thread=thread, from_agent="cursor", to_agent="maestro", subject="CONSULT|c", body="c"
    )
    advance_scan(
        "endpoint-wake-dispatcher", a_id, c_id,
        [{"agent": "maestro", "thread_id": thread, "turn_id": c_id, "turn_number": c_n}],
        now="2020-01-01 10:00:10",
    )
    assert claim_due_wakes(now="2020-01-01 10:00:12")["wake_id"] is None
    reply_id, _, _, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="REPLY|m",
        body="m",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01T10:00:15Z", reply_id),
        )
    early = claim_due_wakes(now="2020-01-01 10:00:20")
    assert early["wake_id"]
    assert [r["turn_id"] for r in early["rows"] if r.get("state") == "claimed"] == [c_id]


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


def test_resolve_delivery_overlays_stale_subscription_display(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale DB display must not beat live grok-window-routing after promote."""
    from cursor_bridge import grok_window_bindings as bindings

    from bus_watch.endpoint_wake_dispatcher import _resolve_delivery_route

    path = tmp_path / "grok-window-routing.json"
    monkeypatch.setattr(bindings, "state_path", lambda: path)
    monkeypatch.setattr(bindings, "sync_endpoint_subscriptions", lambda **_: {"updated": []})
    bindings.promote("10", updated_by="test")

    class Store:
        def subscription(self, agent: str, thread_id: str):
            if agent == "grok-bot-gc1":
                return (
                    "wildcard",
                    {
                        "id": 7,
                        "surface": "grok",
                        "display": "9",
                        "title": "Stage Manager (gc1)",
                        "agent_id": None,
                    },
                )
            return None

    route, sub = _resolve_delivery_route(Store(), "grok-bot-gc1", "17927")  # type: ignore[arg-type]
    assert route == "wildcard"
    assert sub["display"] == "10"


def test_fire_due_wakes_insyncerator_ledger_finds_grok_bot_gc2_subscription(db: None) -> None:
    """Mailbox canonical agent in ledger must still resolve grok-bot-gc2 subscriptions."""
    from unittest.mock import patch

    detail = create_thread(thread_id=None, slug="gc2-mailbox")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="grok-bot-gc2",
        subject="SYNC-PLAN|gc1->insyncerator|step 1",
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
                "agent": "insyncerator",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:01")
    assert claimed["wake_id"]
    posts: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            if agent == "insyncerator":
                return None
            if agent == "grok-bot-gc2":
                return (
                    "wildcard",
                    {
                        "id": 24,
                        "surface": "grok",
                        "display": "10",
                        "title": "insyncerator",
                        "agent_id": None,
                    },
                )
            return None

        def result(self, **payload: object) -> int:
            posts.append(dict(payload))
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=("delivered", "match"),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert posts[0]["outcome"] == "delivered"
    assert posts[0]["route"] == "wildcard"
    assert posts[0]["subscription_id"] == 24


def test_fire_due_wakes_passes_coalesced_turn_numbers_to_display_post(db: None) -> None:
    from unittest.mock import patch

    detail = create_thread(thread_id=None, slug="multi-turn-wake")
    assert detail is not None
    thread = str(detail["id"])
    pending: list[dict] = []
    last_id = 0
    for n in range(4):
        turn_id, _ts, number, _c = insert_turn(
            thread=thread,
            from_agent="cursor",
            to_agent="grok-bot-gc1",
            subject=f"REQUEST|{n}",
            body="b",
        )
        last_id = turn_id
        pending.append(
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        )
    numbers = [p["turn_number"] for p in pending]
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan("endpoint-wake-dispatcher", 0, last_id, pending, now="2020-01-01 00:00:00")
    claimed = claim_due_wakes(now="2020-01-01 00:00:01")
    assert claimed["wake_id"]
    turn_args: list[int | str] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            if agent == "grok-bot-gc1":
                return (
                    "wildcard",
                    {
                        "id": 1,
                        "surface": "grok",
                        "display": "10",
                        "title": "Stage Manager (gc1)",
                        "agent_id": None,
                    },
                )
            return None

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    def _capture(row: dict, sub: dict, turn: int | str) -> tuple[str, str]:
        turn_args.append(turn)
        return ("delivered", "match")

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert len(turn_args) == 1
    expected = ",".join(str(n) for n in sorted(numbers))
    assert turn_args[0] == expected


def test_insyncerator_peer_wake_uses_window_binding_not_cursor_sdk_filter() -> None:
    assert qualifies(
        {"to_agent": "insyncerator", "from_agent": "grok-bot", "subject": "WAKE|inbox"},
        cse=False,
    )
    assert qualifies(
        {"to_agent": "syncerator", "from_agent": "grok-bot-gc1", "subject": "REQUEST|sync"},
        cse=False,
    )
    assert not qualifies(
        {"to_agent": "insyncerator", "from_agent": "cursor-sdk", "subject": "PICKUP|x"},
        cse=False,
    )

    class Store:
        def __init__(self) -> None:
            self.pending: list = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 7,
                    "to_agent": "insyncerator",
                    "from_agent": "grok-bot",
                    "thread": "16416",
                    "turn_number": 1,
                    "subject": "WAKE|inbox",
                }
            ]

        def subscription(self, agent: str, thread: str):
            return None

        def scan(self, high: int, new_hw: int, pending: list) -> None:
            self.pending = pending

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert store.pending[0]["agent"] == "insyncerator"
    assert store.pending[0]["thread_id"] == "16416"


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


def test_fire_maestro_cse_routes_holder_agent_id(db: None) -> None:
    from unittest.mock import patch

    detail = create_thread(thread_id=None, slug="maestro-cse")
    assert detail is not None
    bus_thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=bus_thread,
        from_agent="cursor",
        to_agent="maestro",
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
                "agent": "maestro",
                "thread_id": bus_thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:01")
    assert claimed["wake_id"]
    holder = {
        "chat_url": "https://claude.ai/chat/maestro",
        "registration_id": "reg-maestro",
    }
    bodies: list[str] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def holder(self, lane_thread_id: str) -> dict:
            assert lane_thread_id == "12286"
            return holder

        def subscription(self, agent: str, thread_id: str):
            return (
                "wildcard",
                {
                    "id": 9,
                    "surface": "cse",
                    "display": "cse",
                    "agent_id": "12286",
                    "title": None,
                },
            )

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    def _capture(induction: str, root_id: str, **kwargs: object) -> dict:
        bodies.append(induction)
        return {
            "ok": True,
            "dry_run": kwargs.get("dry_run"),
            "registration_id": "reg-maestro",
            "url": holder["chat_url"],
            "body": {
                "chat_url": holder["chat_url"],
                "registration_id": "reg-maestro",
            },
        }

    with patch("bus_watch.cse_followup.fire_cse_followup", side_effect=_capture):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert bodies[0] == f"wake thread {bus_thread} turns {number}"
    assert bodies[1] == f"wake thread {bus_thread} turns {number}"


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


