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
    assert qualifies(
        {"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "CONSULT|x"},
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "grok-bot-aab1",
            "subject": "ASK|escrow in opening only?",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "grok-bot-aab1",
            "subject": "RESULT|escrow-in-opening panel",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "grok-bot-gc1",
            "subject": "NUDGE|16934#11 ASK",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "web-drive-impresario",
            "subject": "PLAN r2|web-drive-impresario",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "grok-bot-gc1",
            "subject": "AMEND #712|Composio mail scope",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "cursor",
            "subject": "CONSULT_REPLY|x",
        },
        cse=True,
    )
    assert qualifies(
        {"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "MEMO|x"},
        cse=True,
    )
    assert not qualifies(
        {"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "STATUS|x"},
        cse=True,
    )
    assert not qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "grok-bot-gc1",
            "subject": "STATUS|agent-bus:5222",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "grok-bot-gc1",
            "subject": "STATUS|ack-batch|leg1",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "grok-bot-gc1",
            "subject": "STATUS | ack-batch | leg1",
        },
        cse=True,
    )
    assert not qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "cursor",
            "subject": "STATUS|ack-batch|leg1",
        },
        cse=True,
    )
    for subj in ("FACT r2|x", "PICKUP r2|x", "STATUS r2|x"):
        assert not qualifies(
            {
                "to_agent": "maestro",
                "from_agent": "grok-bot-gc1",
                "subject": subj,
            },
            cse=True,
        )
    assert not qualifies(
        {"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "ACK|x"},
        cse=True,
    )
    assert not qualifies(
        {"to_agent": "maestro", "from_agent": "grok-bot-gc1", "subject": "PICKUP|x"},
        cse=True,
    )
    assert qualifies(
        {"to_agent": "maestro", "from_agent": "cursor", "subject": "CHECKPOINT|x"},
        cse=True,
    )
    assert not qualifies(
        {"to_agent": "maestro", "from_agent": "cursor-sdk", "subject": "CLOSEOUT|x"},
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "cursor",
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk CLOSEOUT a6c1430199ea-a24aece5 contract=freeform",
        },
        cse=False,
    )
    assert not qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "cursor-sdk",
            "subject": "DONE #359 RAG without pipe",
        },
        cse=True,
    )
    assert qualifies(
        {
            "to_agent": "web-anthropic",
            "from_agent": "cursor",
            "subject": "TYPE: DIRECTIVE — go",
        },
        cse=True,
    )
    assert not qualifies(
        {"to_agent": "web-anthropic", "from_agent": "cursor", "subject": "FACT|x"},
        cse=True,
    )
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "MEMO|x"},
        cse=False,
    )
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "FACT|x"},
        cse=False,
    )
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "RULING|x"},
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-aab1",
            "from_agent": "grok-bot-gc1",
            "subject": "RELAY|maestro ACK",
        },
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor-sdk",
            "subject": "RESULT|39359",
        },
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-r1",
            "from_agent": "web-drive-impresario",
            "subject": "REVIEW-REQUEST|R1",
        },
        cse=False,
    )
    assert subject_allowed("TYPE: ROUTE/CONTROL", cse=False)
    assert not subject_allowed("TYPE: ROUTE/CONTROL", cse=True)
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "CHECKPOINT|x"},
        cse=False,
    )
    assert qualifies(
        {"to_agent": "grok-bot-gc1", "from_agent": "cursor", "subject": "STATUS|x"},
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor",
            "subject": "cursor-sdk generate admitted",
        },
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
        "EAR",
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
        "EAR",
    }
    assert not qualifies(
        {"to_agent": "all", "from_agent": "cursor", "subject": "DONE|x"}, cse=False
    )
    assert not qualifies(
        {"to_agent": "cursor", "from_agent": "cursor", "subject": "DONE|x"}, cse=False
    )
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
        assert not qualifies(
            {
                "to_agent": "maestro",
                "from_agent": "grok-bot-bl1",
                "subject": intermediate_subj,
            },
            cse=True,
        )
        assert qualifies(
            {
                "to_agent": "grok-bot-gc1",
                "from_agent": "grok-bot-bl1",
                "subject": intermediate_subj,
            },
            cse=False,
        )
        assert not qualifies(
            {
                "to_agent": "maestro",
                "from_agent": "cursor-sdk",
                "subject": intermediate_subj,
            },
            cse=True,
        )
    # ACK/ROUTE receipts to grok-bot-gc1 or maestro do not wake them, while ACK to an impresario house still wakes it
    assert not qualifies(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "grok-bot-bl1",
            "subject": "ACK|work-1",
        },
        cse=False,
    )
    assert qualifies(
        {
            "to_agent": "grok-bot-bl1",
            "from_agent": "grok-bot-gc1",
            "subject": "ACK|work-1",
        },
        cse=False,
    )
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
    freeform_closeout = "cursor-sdk CLOSEOUT 940a9f86d053-8e0c8ad5 contract=freeform"
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
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|x",
        body="b",
    )
    item = {
        "agent": "grok-bot-gc1",
        "thread_id": thread,
        "turn_id": turn_id,
        "turn_number": number,
    }
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher", 0, turn_id, [item], now="2020-01-01 00:00:00"
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")["wake_id"]
    record_wake_result(
        wake, "pending", "active_chat_busy", "exact", 1, None, now="2020-01-01 00:00:02"
    )
    assert claim_due_wakes(now="2020-01-01 00:00:30")["wake_id"] is None
    again = claim_due_wakes(now="2020-01-01 00:01:03")
    assert again["wake_id"]
    record_wake_result(
        again["wake_id"],
        "unknown",
        "paste_failed",
        "exact",
        1,
        None,
        now="2020-01-01 00:01:04",
    )
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
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|a",
        body="a",
    )
    get_cursor()
    with connect() as conn:
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
    wake = claim_due_wakes(now="2020-01-01 10:00:01")
    record_wake_result(
        wake["wake_id"], "delivered", "ok", "exact", 1, None, now="2020-01-01 10:00:02"
    )
    b_id, _tb, b_n, _cb = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|b",
        body="b",
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
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|c",
        body="c",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        b_id,
        c_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": c_id,
                "turn_number": c_n,
            }
        ],
        now="2020-01-01 10:00:30",
    )
    early = claim_due_wakes(now="2020-01-01 10:00:40")
    assert early["wake_id"]
    assert [r["turn_id"] for r in early["rows"] if r.get("state") == "claimed"] == [
        b_id,
        c_id,
    ]


def test_cse_route_debounce_cleared_by_web_anthropic_reply(db: None) -> None:
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="deb-cse-reply")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _t, a_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="CONSULT|a",
        body="a",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 10:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 10:00:01")
    record_wake_result(
        wake["wake_id"], "delivered", "ok", "cse", 9, "reg-1", now="2020-01-01 10:00:02"
    )
    c_id, _t3, c_n, _c3 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="maestro",
        subject="CONSULT|c",
        body="c",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        c_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": c_id,
                "turn_number": c_n,
            }
        ],
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
    assert [r["turn_id"] for r in early["rows"] if r.get("state") == "claimed"] == [
        c_id
    ]


def test_debounce_second_turn_waits(db: None) -> None:
    detail = create_thread(thread_id=None, slug="deb")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _t, a_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|a",
        body="a",
    )
    b_id, _t2, b_n, _c2 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|b",
        body="b",
    )
    get_cursor()
    with connect() as conn:
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
    wake = claim_due_wakes(now="2020-01-01 00:00:01")
    record_wake_result(
        wake["wake_id"], "delivered", "ok", "exact", 1, None, now="2020-01-01 00:00:02"
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
        now="2020-01-01 00:00:30",
    )
    assert claim_due_wakes(now="2020-01-01 00:00:40")["wake_id"] is None
    later = claim_due_wakes(now="2020-01-01 00:10:03")
    assert [r["turn_id"] for r in later["rows"]] == [b_id]


def test_outcome_table() -> None:
    assert (
        classify_display(200, {"ok": True, "delivered": True}, None)[0] == "delivered"
    )
    assert (
        classify_display(200, {"ok": False, "pasted": True, "delivered": False}, None)[
            0
        ]
        == "unknown"
    )
    assert (
        classify_display(
            200, {"ok": False, "reason": "paste_failed", "pasted": False}, None
        )[0]
        == "unknown"
    )
    assert (
        classify_display(
            200, {"ok": False, "reason": "active_chat_busy", "pasted": False}, None
        )[0]
        == "pending"
    )
    assert classify_display(
        200, {"ok": False, "reason": "no_composer", "pasted": False}, None
    ) == (
        "pending",
        "no_composer",
    )
    assert classify_display(500, {"ok": False}, None)[0] == "unknown"
    err = httpx.ConnectError("down")
    assert classify_display(0, None, err)[0] == "pending"
    assert (
        classify_cse({"ok": False, "error": "cdp-ask unreachable: x"})[0] == "unknown"
    )
    assert classify_cse({"ok": False})[0] == "unknown"
    assert (
        classify_cse({"ok": False, "error": "cdp-ask HTTP 502", "status_code": 502})[0]
        == "refused"
    )


def test_fire_holders_exception_records_unknown_and_does_not_propagate(
    db: None,
) -> None:
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

        def subscription(self, agent: str, thread_id: str) -> None:
            return None

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
    monkeypatch.setattr(
        bindings, "sync_endpoint_subscriptions", lambda **_: {"updated": []}
    )
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


def test_fire_due_wakes_insyncerator_ledger_finds_grok_bot_gc2_subscription(
    db: None,
) -> None:
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
    advance_scan(
        "endpoint-wake-dispatcher", 0, last_id, pending, now="2020-01-01 00:00:00"
    )
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
        {
            "to_agent": "syncerator",
            "from_agent": "grok-bot-gc1",
            "subject": "REQUEST|sync",
        },
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

        def subscription(self, agent: str, thread_id: str) -> None:
            return None

        def holder(self, thread: str) -> dict:
            raise httpx.ConnectError("giw holders down")

    with pytest.raises(httpx.ConnectError):
        scan_once(Boom())  # type: ignore[arg-type]


def _maestro_seat_registration_turns() -> list[dict[str, object]]:
    from hop_handoff.body import build_seat_registration_stamp

    stamp_body = build_seat_registration_stamp(
        successor_birth_id="a" * 32,
        registration_id="reg-maestro",
        execution_id="exec-test",
        parent_thread="12286",
        chat_url="https://claude.ai/chat/maestro",
    )
    _tid, _ts, reg_num, _c = insert_turn(
        thread="12286",
        from_agent="cursor-auto",
        to_agent="cursor",
        subject="SEAT_REGISTRATION",
        body=stamp_body,
    )
    return [
        {
            "turn_number": reg_num,
            "body": stamp_body,
        }
    ]


def _maestro_cse_wake_store(
    *,
    claimed: dict,
    holder: dict[str, str],
    seat_registration_turns: list[dict[str, object]] | None,
) -> tuple[object, list[dict[str, object]], list[str]]:
    alerts: list[dict[str, object]] = []
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

        def turns(self, thread: str, **params: object) -> dict:
            assert thread == "12286"
            return {"turns": seat_registration_turns or []}

        def create_turn(
            self,
            *,
            thread: str,
            from_agent: str,
            to_agent: str,
            subject: str,
            body: str,
        ) -> dict:
            alerts.append(
                {
                    "thread": thread,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "subject": subject,
                    "body": body,
                }
            )
            return {"id": len(alerts)}

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    return Store(), alerts, bodies


def test_fire_maestro_cse_routes_holder_agent_id(db: None) -> None:
    from unittest.mock import patch

    create_thread(thread_id=12286, slug="maestro-lane")
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
    store, alerts, bodies = _maestro_cse_wake_store(
        claimed=claimed,
        holder=holder,
        seat_registration_turns=_maestro_seat_registration_turns(),
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
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert bodies[0] == f"wake thread {bus_thread} turns {number}"
    assert bodies[1] == f"wake thread {bus_thread} turns {number}"
    assert alerts == []


@pytest.mark.parametrize(
    ("holder", "case"),
    [
        (
            {
                "chat_url": "https://claude.ai/chat/stale",
                "registration_id": "reg-stale",
            },
            "stale_holder",
        ),
        (
            {
                "chat_url": "https://claude.ai/chat/lane-16416",
                "registration_id": "reg-16416",
            },
            "foreign_holder",
        ),
    ],
    ids=["stale_holder", "foreign_holder"],
)
def test_fire_maestro_cse_stale_and_foreign_holder_skips_delivery_and_alerts(
    db: None, holder: dict[str, str], case: str
) -> None:
    from unittest.mock import patch

    _ = case
    create_thread(thread_id=12286, slug="maestro-lane-refuse")
    detail = create_thread(thread_id=None, slug=f"maestro-cse-{case}")
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
    store, alerts, bodies = _maestro_cse_wake_store(
        claimed=claimed,
        holder=holder,
        seat_registration_turns=_maestro_seat_registration_turns(),
    )
    fire_calls: list[object] = []

    def _forbidden(*args: object, **kwargs: object) -> dict:
        fire_calls.append((args, kwargs))
        return {"ok": True}

    with patch("bus_watch.cse_followup.fire_cse_followup", side_effect=_forbidden):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert fire_calls == []
    assert bodies == []
    assert len(alerts) == 2
    assert {a["to_agent"] for a in alerts} == {"maestro", "grok-bot-gc1"}
    assert all(a["subject"] == "FACT|wake-registration-refused" for a in alerts)
    assert all(a["from_agent"] == "agent_bus" for a in alerts)
    assert "rejected_registration_id:" in alerts[0]["body"]
    assert "stamp_registration_id: reg-maestro" in alerts[0]["body"]


def test_fire_maestro_cse_missing_seat_registration_stamp_skips_and_alerts(
    db: None,
) -> None:
    from unittest.mock import patch

    create_thread(thread_id=12286, slug="maestro-lane-no-stamp")
    detail = create_thread(thread_id=None, slug="maestro-cse-no-stamp")
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
    holder = {
        "chat_url": "https://claude.ai/chat/maestro",
        "registration_id": "reg-maestro",
    }
    store, alerts, _bodies = _maestro_cse_wake_store(
        claimed=claimed,
        holder=holder,
        seat_registration_turns=[],
    )
    fire_calls: list[object] = []

    def _forbidden(*args: object, **kwargs: object) -> dict:
        fire_calls.append((args, kwargs))
        return {"ok": True}

    with patch("bus_watch.cse_followup.fire_cse_followup", side_effect=_forbidden):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert fire_calls == []
    assert len(alerts) == 2


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
        supervise(
            App(), "endpoint_wake_dispatcher", loop, restart=True, max_restarts=None
        )
        await asyncio.sleep(1.2)
        assert calls["n"] >= 2

    asyncio.run(run())


def test_specimen_18460_no_composer_records_pending_and_drops_dead_agent_id(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """18460#176 specimen: exact cursor route + subscribed agent_id + no_composer -> pending & unsubscribed."""
    from unittest.mock import AsyncMock, patch

    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        resolve_endpoint,
        subscribe_endpoint,
        unsubscribe_endpoint,
    )
    from cursor_bridge import wake_delivery as wd
    from cursor_bridge.chat_surface import classify_surface
    from cursor_bridge.wake_delivery import deliver_wake

    from bus_watch import ide_hop_land

    monkeypatch.setattr(ide_hop_land, "WATCH_DIR", tmp_path)
    detail = create_thread(thread_id=18460, slug="maestro-general-work")
    assert detail is not None
    thread = str(detail["id"])
    dead_agent_id = "4a3412d2-dd68-4510-9953-e92fe72cd720"
    title = f"{thread} maestro-general-work"
    sub_res = subscribe_endpoint(
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=title,
        agent_id=dead_agent_id,
        bound_by="liaison-tick-claim",
    )
    sub_row = sub_res["row"]
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="REQUEST|freeform|decision:operator-observability-ledger — K43 of record",
        body="TYPE: REQUEST\nBody",
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
                "agent": "cursor",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2026-10-20 22:59:56",
    )
    claimed = claim_due_wakes(now="2026-10-20 22:59:57")
    assert claimed["wake_id"]

    # Verify wake_delivery normalizes missing Glass composer handle to reason=no_composer
    surface_match = classify_surface(
        [],
        [{"text": title, "selected": True, "agent_id": dead_agent_id}],
        composer_draft="",
    )
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", AsyncMock(return_value=surface_match))

    class _NoHandleWS:
        async def send(self, _raw: str) -> None:
            return None

        async def recv(self) -> str:
            return '{"id": 1, "result": {"result": {"value": {"ok": false, "reason": "no_handle"}}}}'

        async def __aenter__(self) -> _NoHandleWS:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *_a, **_kw: _NoHandleWS())
    wake_out = asyncio.run(
        deliver_wake(
            display="8",
            app="cursor",
            thread=thread,
            title=title,
            agent_id=dead_agent_id,
            turn=number,
        )
    )
    assert wake_out["ok"] is False
    assert wake_out["reason"] == "no_composer"
    assert wake_out["expected_agent_id"] == dead_agent_id

    unsub_calls: list[tuple[str, str, str | None, str | None]] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            return resolve_endpoint(agent, thread_id)

        def unsubscribe(
            self,
            agent: str,
            thread_scope: str,
            *,
            bound_by: str = "endpoint-wake-dispatcher",
            evidence: str | None = None,
            expected_agent_id: str | None = None,
        ) -> dict:
            unsub_calls.append((agent, thread_scope, bound_by, evidence))
            return unsubscribe_endpoint(
                agent=agent,
                thread_scope=thread_scope,
                bound_by=bound_by,
                evidence=evidence,
                expected_agent_id=expected_agent_id,
            )

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
                now="2026-10-20 22:59:57",
            )

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=classify_display(200, wake_out, None),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]

    with connect() as conn:
        ledger_row = conn.execute(
            "SELECT state, reason, route, subscription_id, finished_at "
            "FROM endpoint_wake_ledger WHERE wake_id=?",
            (claimed["wake_id"],),
        ).fetchone()
    assert ledger_row is not None
    assert ledger_row["state"] == "pending"
    assert ledger_row["reason"] == "no_composer"
    assert ledger_row["route"] == "exact"
    assert ledger_row["subscription_id"] == sub_row["id"]
    assert ledger_row["finished_at"] == "2026-10-20 22:59:57"

    assert len(unsub_calls) == 1
    assert unsub_calls[0][0] == "cursor"
    assert unsub_calls[0][1] == thread
    assert resolve_endpoint("cursor", thread) is None
    cur_sub = current_subscription("cursor", thread)
    assert cur_sub is not None
    assert cur_sub["state"] == "unsubscribed"


def test_fired_hop_past_deadline_with_null_landed_transcript_keeps_display_subscription(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fired hop with no landed transcript expires and leaves the display subscription."""
    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        resolve_endpoint,
        subscribe_endpoint,
        unsubscribe_endpoint,
    )

    from bus_watch import ide_hop_land
    from bus_watch.ide_hop_land import (
        expire_overdue_hop,
        mark_fired,
        read_hop_land,
    )

    monkeypatch.setattr(ide_hop_land, "WATCH_DIR", tmp_path)
    detail = create_thread(thread_id=18460, slug="maestro-general-work")
    assert detail is not None
    thread = str(detail["id"])
    dead_agent_id = "4a3412d2-dd68-4510-9953-e92fe72cd720"
    departing_id = "cded3450-5686-44c8-af7c-c1c7c4c43618"
    subscribe_endpoint(
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} maestro-general-work",
        agent_id=dead_agent_id,
        bound_by="liaison-tick-claim",
    )
    mark_fired(
        root=thread,
        tip_cp=153,
        seal_bus_turn=153,
        departing_transcript_id=departing_id,
        fired_at=1000.0,
        deadline_s=600.0,
        message_path="liaison-18460-20261020T194538Z.md",
        watch_dir=tmp_path,
    )
    # Before deadline (t=1500 < 1600): still fired, subscription still intact.
    assert expire_overdue_hop(thread, now=1500.0, watch_dir=tmp_path) is None
    assert resolve_endpoint("cursor", thread) is not None

    # Past deadline (t=2000 >= 1600) with no landed transcript: expire, keep the row.
    expired = expire_overdue_hop(thread, now=2000.0, watch_dir=tmp_path)
    assert expired is not None
    assert expired["state"] == "expired"
    assert expired["landed_transcript_id"] is None
    persisted = read_hop_land(thread, watch_dir=tmp_path)
    assert persisted is not None and persisted["state"] == "expired"
    assert resolve_endpoint("cursor", thread) is not None
    cur_sub = current_subscription("cursor", thread)
    assert cur_sub is not None
    assert cur_sub["state"] == "subscribed"
    assert "hop_expired" not in str(cur_sub.get("evidence") or "")

    # Also verify scan_once reaps an overdue fired hop before resolving delivery route.
    subscribe_endpoint(
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} maestro-general-work",
        agent_id=dead_agent_id,
        bound_by="liaison-tick-claim",
    )
    mark_fired(
        root=thread,
        tip_cp=153,
        seal_bus_turn=153,
        departing_transcript_id=departing_id,
        fired_at=1000.0,
        deadline_s=10.0,
        message_path="liaison-18460-20261020T194538Z.md",
        watch_dir=tmp_path,
    )

    class ScanStore:
        def __init__(self) -> None:
            self.pending: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 166000,
                    "to_agent": "cursor",
                    "from_agent": "maestro",
                    "thread": thread,
                    "turn_number": 176,
                    "subject": "REQUEST|freeform|decision:operator-observability-ledger",
                }
            ]

        def subscription(self, agent: str, thread_id: str):
            return resolve_endpoint(agent, thread_id)

        def unsubscribe(
            self,
            agent: str,
            thread_scope: str,
            *,
            bound_by: str = "endpoint-wake-dispatcher",
            evidence: str | None = None,
            expected_agent_id: str | None = None,
        ) -> dict:
            return unsubscribe_endpoint(
                agent=agent,
                thread_scope=thread_scope,
                bound_by=bound_by,
                evidence=evidence,
                expected_agent_id=expected_agent_id,
            )

        def scan(self, high: int, new_hw: int, pending: list) -> None:
            self.pending = pending

    store = ScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert store.pending
    assert resolve_endpoint("cursor", thread) is not None
    kept = current_subscription("cursor", thread)
    assert kept is not None
    assert kept["state"] == "subscribed"
    assert read_hop_land(thread, watch_dir=tmp_path)["state"] == "expired"  # type: ignore[index]


def test_mark_expired_with_landed_transcript_drops_different_agent(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded landed id that differs from the live agent is still unsubscribed."""
    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        subscribe_endpoint,
    )

    from bus_watch import ide_hop_land
    from bus_watch.ide_hop_land import mark_expired, mark_fired

    monkeypatch.setattr(ide_hop_land, "WATCH_DIR", tmp_path)
    detail = create_thread(thread_id=18461, slug="landed-differs")
    assert detail is not None
    thread = str(detail["id"])
    live_agent = "4a3412d2-dd68-4510-9953-e92fe72cd720"
    subscribe_endpoint(
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} landed-differs",
        agent_id=live_agent,
        bound_by="liaison-tick-claim",
    )
    record = mark_fired(
        root=thread,
        tip_cp=1,
        seal_bus_turn=1,
        departing_transcript_id="departing",
        fired_at=1000.0,
        deadline_s=600.0,
        message_path="liaison-18461.md",
        watch_dir=tmp_path,
    )
    record["landed_transcript_id"] = "landed-other"
    mark_expired(record, watch_dir=tmp_path)
    cur = current_subscription("cursor", thread)
    assert cur is not None
    assert cur["state"] == "unsubscribed"
    assert str(cur.get("evidence") or "").startswith("hop_expired:")


def test_grok_wildcard_no_composer_retries_pending_without_unsubscribing(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wildcard Grok route with no_composer records pending and does not unsubscribe '*'."""
    from unittest.mock import patch

    from agent_bus_store.db.endpoint_subscriptions import (
        resolve_endpoint,
        subscribe_endpoint,
        unsubscribe_endpoint,
    )

    from bus_watch import ide_hop_land

    monkeypatch.setattr(ide_hop_land, "WATCH_DIR", tmp_path)
    detail = create_thread(thread_id=None, slug="grok-no-composer")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="stage-manager",
        thread_scope="*",
        surface="grok",
        display="9",
        title="Stage Manager",
    )
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="stage-manager",
        subject="REQUEST|sync",
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
                "agent": "stage-manager",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2026-10-20 23:00:00",
    )
    claimed = claim_due_wakes(now="2026-10-20 23:00:01")
    assert claimed["wake_id"]
    unsub_calls: list[tuple[str, str]] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            return resolve_endpoint(agent, thread_id)

        def unsubscribe(
            self,
            agent: str,
            thread_scope: str,
            *,
            bound_by: str = "endpoint-wake-dispatcher",
            evidence: str | None = None,
            expected_agent_id: str | None = None,
        ) -> dict:
            unsub_calls.append((agent, thread_scope))
            return unsubscribe_endpoint(
                agent=agent,
                thread_scope=thread_scope,
                bound_by=bound_by,
                evidence=evidence,
                expected_agent_id=expected_agent_id,
            )

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
                now="2026-10-20 23:00:01",
            )

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=("pending", "no_composer"),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]

    assert unsub_calls == []
    assert resolve_endpoint("stage-manager", thread) is not None
