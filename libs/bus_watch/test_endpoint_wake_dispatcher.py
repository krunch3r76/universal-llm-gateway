"""Dispatcher allowlist, debounce, retry, lifespan, and outcome table."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    CONSUMER,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.server import create_app
from agent_bus_store.testing.wake_cursor_test_support import rewind_wake_cursor_for_test
from background_supervisor import supervise

from bus_watch.endpoint_wake_dispatcher import (
    CSE_WAKE_SUBJECT_HEADS,
    GROK_WAKE_SUBJECT_HEADS,
    _declared_wake_rule_ids,
    _effective_to_agent,
    _legacy_qualifies,
    _reset_no_composer_state,
    _subject_allowed_rule_id,
    classify_cse,
    classify_display,
    fire_due_wakes,
    qualifies,
    qualify,
    scan_once,
    subject_allowed,
)
from bus_watch.endpoint_wake_transport import StoreClient


@pytest.fixture(autouse=True)
def _houseless_resolve_default_non_houseless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline tests default to ledger path unless they patch houseless resolution."""
    from cdp_ask.houseless_resolve import HouselessResolution

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=False, reason="purpose_not_houseless"),
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
    admit_echo = qualify(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor",
            "subject": "cursor-sdk generate admitted",
        },
        cse=False,
    )
    assert admit_echo.wake is False
    assert admit_echo.rule_id == "drop_dispatch_intermediate"
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
        "RULING",
    }
    assert qualifies(
        {
            "to_agent": "maestro",
            "from_agent": "grok-coordinator-1",
            "subject": "RULING|40218|wake path",
        },
        cse=True,
    )
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
        "ANSWER",
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


@pytest.mark.parametrize(
    "subject",
    [
        "REQUEST: a:1 x",
        "REQUEST x",
        "DONE: x",
        "REQUEST: x | y",
    ],
)
def test_grok_nopipe_request_head_wakes(subject: str) -> None:
    assert (
        _subject_allowed_rule_id(subject, cse=False, to_agent="grok-bot-gc1")
        == "wake_grok_allowlist"
    )


def test_cse_nopipe_request_head_suppressed() -> None:
    assert (
        _subject_allowed_rule_id("REQUEST x", cse=True, to_agent="grok-bot-gc1") is None
    )


@pytest.mark.parametrize(
    "subject",
    ["Requesting x", "Review of x"],
)
def test_nopipe_prose_first_token_still_suppressed(subject: str) -> None:
    assert _subject_allowed_rule_id(subject, cse=False, to_agent="grok-bot-gc1") is None


def test_cdp_delivery_subjects_unchanged() -> None:
    assert (
        _subject_allowed_rule_id(
            "cdp reply — 1234abcd", cse=False, to_agent="grok-bot-gc1"
        )
        == "wake_gate_or_directive"
    )
    assert (
        _subject_allowed_rule_id(
            "cdp generate — rid", cse=False, to_agent="grok-bot-gc1"
        )
        is None
    )


def test_grok_wake_heads_set() -> None:
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


def test_compound_ack_route_coord_fallthrough_a41157() -> None:
    """Friction a:41157 — ACK| + REQUEST| (and siblings) must not drop_ack_route_coord."""
    base = {
        "to_agent": "grok-bot-gc1",
        "from_agent": "web-anthropic",
        "thread": "41157",
        "id": 1,
    }
    v_ack_req = qualify({**base, "subject": "ACK|x + REQUEST|y"}, cse=False)
    assert v_ack_req.wake is True
    assert v_ack_req.rule_id != "drop_ack_route_coord"
    v_route_q = qualify({**base, "subject": "ROUTE|a + QUESTION|b"}, cse=False)
    assert v_route_q.wake is True
    assert v_route_q.rule_id != "drop_ack_route_coord"
    v_ack_blocked = qualify({**base, "subject": "ACK|a + BLOCKED|b"}, cse=False)
    assert v_ack_blocked.wake is True
    assert v_ack_blocked.rule_id != "drop_ack_route_coord"
    v_plain_ack = qualify({**base, "subject": "ACK|x"}, cse=False)
    assert v_plain_ack.wake is False
    assert v_plain_ack.rule_id == "drop_ack_route_coord"


def test_scan_once_emits_endpoint_wake_suppressed_for_subscribed_thread(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
    from agent_bus_store.events import publisher
    from agent_bus_store.server import wire_bus_watch_event_publisher

    queued: list[dict[str, object]] = []

    def _capture_put(self, line: str) -> None:
        queued.append(json.loads(line.strip()))

    monkeypatch.setattr(publisher._UDSPublisher, "put_nowait", _capture_put)
    wire_bus_watch_event_publisher()
    detail = create_thread(thread_id=None, slug="41157-suppress-emit")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="grok-bot-gc1",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title="t",
        agent_id="gc1-id",
        bound_by="test",
    )
    insert_turn(
        thread=thread,
        from_agent="grok-bot-bl1",
        to_agent="grok-bot-gc1",
        subject="ACK|plain-only",
        body="ack",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("display", row)
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, lane: str) -> str | None:
            return None

    scan_once(Store())  # type: ignore[arg-type]
    suppressed_events = [
        e for e in queued if e.get("signal") == "endpoint_wake.suppressed"
    ]
    assert len(suppressed_events) == 1
    payload = suppressed_events[0].get("payload")
    assert isinstance(payload, dict)
    assert payload.get("rule_id") == "drop_ack_route_coord"
    assert payload.get("to_agent") == "grok-bot-gc1"
    assert payload.get("thread_id") == thread
    assert payload.get("subject_head") == "ACK"
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


def test_maestro_direct_address_pointer_on_12286_qualifies() -> None:
    """Specimen 12286#4854: closeout substring must not suppress direct to=maestro on 12286."""
    turn = {
        "to_agent": "maestro",
        "from_agent": "cursor",
        "subject": "pointer: :4/:5 probe closeout",
        "thread": "12286",
        "id": 4854,
        "turn_number": 4854,
    }
    verdict = qualify(turn, cse=True)
    assert verdict.wake is True
    assert verdict.rule_id == "wake_maestro_direct_address"


def test_maestro_direct_address_closeout_on_held_lane() -> None:
    """Specimen 20729#1: held lane wakes on CLOSEOUT vocabulary without milestone head."""

    class _HolderStore:
        def holder(self, lane: str) -> dict[str, str] | None:
            if lane == "20729":
                return {"registration_id": "reg-held"}
            return None

    turn = {
        "to_agent": "maestro",
        "from_agent": "cursor",
        "subject": "CLOSEOUT xvfb :4/:5 read-only probe",
        "thread": "20729",
        "id": 1,
        "turn_number": 1,
    }
    verdict = qualify(turn, cse=True, store=_HolderStore())  # type: ignore[arg-type]
    assert verdict.wake is True
    assert verdict.rule_id == "wake_maestro_direct_address"


def test_grok_impresario_conductor_milestone_and_intermediate_wakes() -> None:
    impresario_targets = (
        "grok-bot-gc1",
        "impresario-career",
        "gc1-pilot-impresario-fleet-action-line",
    )
    milestone_subjects = (
        "cursor-sdk CLOSEOUT abc contract=conductor caller=grok-bot-gc1",
        "stop: PARKED_TRANSPORT — 20547",
        "BLOCKED|40218|gate",
        "FAILED|40218|dispatch",
        "DONE|40218|complete",
        "QUESTION|40218|fork",
        "DONE 40218 complete",
        "FAILED 40218 dispatch",
        "QUESTION 40218 fork",
    )
    for to_agent in impresario_targets:
        for from_agent in ("cursor-sdk", "conductor-hop"):
            for subject in milestone_subjects:
                assert qualifies(
                    {
                        "to_agent": to_agent,
                        "from_agent": from_agent,
                        "subject": subject,
                    },
                    cse=False,
                ), f"expected wake: {from_agent} -> {to_agent} subject={subject!r}"
    intermediate_subjects = (
        "PICKUP|40218",
        "ADMITTED|40218",
        "CHECKPOINT 40218 leg",
        "hop-1 context",
        "park-harvest wake — 20547",
        "cursor-sdk generate admitted",
        (
            "cursor-sdk CLOSEOUT 2d1f2c04d3ff-15c30a1d "
            "contract=conductor caller=conductor-hop"
        ),
    )
    for to_agent in impresario_targets:
        for from_agent in ("cursor-sdk", "conductor-hop"):
            for subject in intermediate_subjects:
                assert not qualifies(
                    {
                        "to_agent": to_agent,
                        "from_agent": from_agent,
                        "subject": subject,
                    },
                    cse=False,
                ), f"expected no wake: {from_agent} -> {to_agent} subject={subject!r}"


def test_git_integration_worker_notice_gap_grok_wake() -> None:
    gap_subject = "NOTICE|gap|abc123def"
    ride_along_subject = "NOTICE|ride_along|abc123def"
    turn_base = {"to_agent": "grok-bot-gc1", "from_agent": "git-integration-worker"}
    assert qualifies({**turn_base, "subject": gap_subject}, cse=False)
    assert not qualifies({**turn_base, "subject": ride_along_subject}, cse=False)
    assert subject_allowed(gap_subject, cse=False, to_agent="grok-bot-gc1")
    assert not subject_allowed(ride_along_subject, cse=False, to_agent="grok-bot-gc1")


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


def test_subscription_depth2_chain_19521_19510_19263() -> None:
    lane_map = {
        "19521": "19510",
        "19510": "19263",
        "19263": None,
    }
    subs = [
        {"id": 1, "state": "subscribed", "thread_scope": "19263"},
        {"id": 2, "state": "subscribed", "thread_scope": "*"},
    ]
    lane_gets: list[str] = []

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> object:
            if path == "/endpoint-subscriptions":
                return {"subscriptions": subs}
            if path.startswith("/threads/") and path.endswith("/lane-current"):
                tid = path.split("/")[2]
                lane_gets.append(path)
                parent = lane_map.get(tid)
                if parent is None:
                    return {"parent_thread": None}
                return {"parent_thread": parent}
            raise AssertionError(f"unexpected _bus {method} {path}")

    result = StubStore().subscription("cursor", "19521")
    assert result is not None
    kind, row = result
    assert kind == "parent"
    assert row["id"] == 1
    assert lane_gets == [
        "/threads/19521/lane-current",
        "/threads/19510/lane-current",
        "/threads/19263/lane-current",
    ]


def test_subscription_rank_and_depth3_cap() -> None:
    lane_map = {
        "19521": "19510",
        "19510": "19263",
        "19263": "90001",
        "90001": "90002",
        "90002": None,
    }
    lane_gets: list[str] = []

    def make_store(extra_subs: list[dict]) -> StoreClient:
        class StubStore(StoreClient):
            def _bus(self, method: str, path: str, **kwargs: object) -> object:
                if path == "/endpoint-subscriptions":
                    return {"subscriptions": extra_subs}
                if path.startswith("/threads/") and path.endswith("/lane-current"):
                    tid = path.split("/")[2]
                    lane_gets.append(path)
                    parent = lane_map.get(tid)
                    if parent is None:
                        return {"parent_thread": None}
                    return {"parent_thread": parent}
                raise AssertionError(f"unexpected _bus {method} {path}")

        return StubStore()

    lane_gets.clear()
    r = make_store(
        [
            {"id": 10, "state": "subscribed", "thread_scope": "90002"},
            {"id": 11, "state": "subscribed", "thread_scope": "*"},
        ]
    ).subscription("cursor", "19521")
    assert r is not None
    assert r[0] == "wildcard"
    assert lane_gets == [
        "/threads/19521/lane-current",
        "/threads/19510/lane-current",
        "/threads/19263/lane-current",
    ]

    lane_gets.clear()
    r = make_store(
        [
            {"id": 20, "state": "subscribed", "thread_scope": "90001"},
            {"id": 21, "state": "subscribed", "thread_scope": "*"},
        ]
    ).subscription("cursor", "19521")
    assert r is not None
    assert r[0] == "parent"
    assert r[1]["thread_scope"] == "90001"

    lane_gets.clear()
    r = make_store(
        [
            {"id": 30, "state": "subscribed", "thread_scope": "19510"},
            {"id": 31, "state": "subscribed", "thread_scope": "19263"},
            {"id": 32, "state": "subscribed", "thread_scope": "90001"},
            {"id": 33, "state": "subscribed", "thread_scope": "*"},
        ]
    ).subscription("cursor", "19521")
    assert r is not None
    assert r[0] == "parent"
    assert r[1]["thread_scope"] == "19510"

    lane_gets.clear()
    r = make_store(
        [
            {"id": 40, "state": "subscribed", "thread_scope": "19521"},
            {"id": 41, "state": "subscribed", "thread_scope": "19510"},
            {"id": 42, "state": "subscribed", "thread_scope": "19263"},
            {"id": 43, "state": "subscribed", "thread_scope": "90001"},
            {"id": 44, "state": "subscribed", "thread_scope": "*"},
        ]
    ).subscription("cursor", "19521")
    assert r is not None
    assert r[0] == "exact"
    assert r[1]["thread_scope"] == "19521"

    lane_gets.clear()
    r = make_store(
        [
            {"id": 50, "state": "subscribed", "thread_scope": "19510"},
            {"id": 55, "state": "subscribed", "thread_scope": "19510"},
        ]
    ).subscription("cursor", "19521")
    assert r is not None
    assert r[1]["id"] == 55


def test_subscription_parent_cycle_stops() -> None:
    lane_map = {"19521": "19510", "19510": "19521"}
    lane_gets: list[str] = []

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> object:
            if path == "/endpoint-subscriptions":
                return {
                    "subscriptions": [
                        {"id": 3, "state": "subscribed", "thread_scope": "19510"},
                    ]
                }
            if path.startswith("/threads/") and path.endswith("/lane-current"):
                tid = path.split("/")[2]
                lane_gets.append(path)
                return {"parent_thread": lane_map[tid]}
            raise AssertionError(f"unexpected _bus {method} {path}")

    result = StubStore().subscription("cursor", "19521")
    assert result is not None
    assert result[0] == "parent"
    assert len(lane_gets) == 2


def test_subscription_http_error_keeps_prior_ancestors() -> None:
    hop = {"n": 0}

    class StubStoreHop2Fail(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> object:
            if path == "/endpoint-subscriptions":
                return {
                    "subscriptions": [
                        {"id": 1, "state": "subscribed", "thread_scope": "19510"},
                        {"id": 2, "state": "subscribed", "thread_scope": "*"},
                    ]
                }
            if path == "/threads/19521/lane-current":
                hop["n"] += 1
                return {"parent_thread": "19510"}
            if path == "/threads/19510/lane-current":
                raise RuntimeError("lane-current down")
            raise AssertionError(f"unexpected _bus {method} {path}")

    result = StubStoreHop2Fail().subscription("cursor", "19521")
    assert result is not None
    assert result[0] == "parent"
    assert result[1]["thread_scope"] == "19510"

    class StubStoreHop1Fail(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> object:
            if path == "/endpoint-subscriptions":
                return {
                    "subscriptions": [
                        {"id": 1, "state": "subscribed", "thread_scope": "19510"},
                    ]
                }
            if path == "/threads/19521/lane-current":
                raise RuntimeError("hop1 down")
            raise AssertionError(f"unexpected _bus {method} {path}")

    assert StubStoreHop1Fail().subscription("cursor", "19521") is None


def test_quiet_with_work_in_flight_routes_to_cursor() -> None:
    subject = "Quiet with work in flight"
    for to_agent in (
        "git-integration-worker",
        "frontier-admit",
        "dispatch",
        "conductor-hop",
        "cursor",
    ):
        assert _effective_to_agent(to_agent, subject) == "cursor"
    for to_agent in (
        "git-integration-worker",
        "frontier-admit",
        "conductor-hop",
        "cursor",
    ):
        assert qualifies(
            {
                "to_agent": to_agent,
                "from_agent": "dispatch",
                "subject": subject,
            },
            cse=False,
        )
    assert not qualifies(
        {"to_agent": "dispatch", "from_agent": "dispatch", "subject": subject},
        cse=False,
    )

    child_thread = "19510-child"

    class Store:
        def __init__(self) -> None:
            self.pending: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, high: int) -> list:
            return [
                {
                    "id": 50,
                    "to_agent": "git-integration-worker",
                    "from_agent": "dispatch",
                    "thread": child_thread,
                    "turn_number": 16,
                    "subject": subject,
                }
            ]

        def subscription(self, agent: str, thread: str):
            if agent == "cursor" and thread == child_thread:
                return (
                    "exact",
                    {
                        "id": 8,
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
    assert len(store.pending) == 1
    assert store.pending[0]["agent"] == "cursor"
    assert store.pending[0]["thread_id"] == child_thread


def test_nest_child_parked_subject_cursor_gate_wake() -> None:
    """Bare PARKED (nest child) on dispatch addresses must wake cursor."""
    parked_subjects = (
        "PARKED (nest child 8082a251) transport idle",
        "PARKED|nest child 8082a251",
        "hop|PARKED (nest child 8082a251)",
    )
    for subject in parked_subjects:
        assert _effective_to_agent("dispatch", subject) == "cursor"
        verdict = qualify(
            {
                "to_agent": "dispatch",
                "from_agent": "cursor-sdk",
                "subject": subject,
            },
            cse=False,
        )
        assert verdict.wake is True
        assert verdict.rule_id == "wake_gate_or_directive"

    assert _effective_to_agent("dispatch", "stop: PARKED_TRANSPORT — 20547") == "cursor"
    assert qualify(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": "stop: PARKED_TRANSPORT",
        },
        cse=False,
    ).wake

    for subject in ("UNPARKED|40218", "status: parked_at=2020-01-01"):
        assert _effective_to_agent("dispatch", subject) == "dispatch"
        assert not qualify(
            {
                "to_agent": "dispatch",
                "from_agent": "cursor-sdk",
                "subject": subject,
            },
            cse=False,
        ).wake

    healthy = (
        "cursor-sdk CLOSEOUT 2d1f2c04d3ff-15c30a1d "
        "contract=conductor caller=conductor-hop"
    )
    assert _effective_to_agent("conductor-hop", healthy) == "conductor-hop"
    assert not qualify(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": healthy,
        },
        cse=False,
    ).wake


def test_degraded_conductor_hop_closeout_routes_to_cursor() -> None:
    healthy = (
        "cursor-sdk CLOSEOUT 2d1f2c04d3ff-15c30a1d "
        "contract=conductor caller=conductor-hop"
    )
    assert _effective_to_agent("conductor-hop", healthy) == "conductor-hop"
    assert not qualifies(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": healthy,
        },
        cse=False,
    )
    degraded = healthy + " degraded exit"
    assert _effective_to_agent("conductor-hop", degraded) == "cursor"
    assert qualifies(
        {
            "to_agent": "conductor-hop",
            "from_agent": "cursor-sdk",
            "subject": degraded,
        },
        cse=False,
    )


def test_composer_defer_blocks_wall_clock_reclaim_until_empty_event(db: None) -> None:
    from bus_watch.endpoint_wake_composer_defer import (
        COMPOSER_DEFER_ARMED,
        notify_composer_empty_for_target,
    )

    detail = create_thread(thread_id=None, slug="composer-defer-reclaim")
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher", 0, turn_id, [item], now="2020-01-01 00:00:00"
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")["wake_id"]
    record_wake_result(
        wake, "pending", "active_chat_busy", "exact", 1, None, now="2020-01-01 00:00:02"
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule FROM endpoint_wake_ledger WHERE wake_id=?",
            (wake,),
        ).fetchone()
    assert row["decided_by_rule"] == COMPOSER_DEFER_ARMED
    assert claim_due_wakes(now="2020-01-01 00:01:03")["wake_id"] is None
    notify_composer_empty_for_target(
        display="9",
        surface="grok",
        agent_id="agent-grok",
        thread_scope=thread,
    )
    again = claim_due_wakes(now="2020-01-01 00:01:04")
    assert again["wake_id"]


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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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


def test_classify_display_hydration_reasons_stay_pending() -> None:
    pending_body = {"ok": False, "pasted": False}
    for reason in ("unknown", "no_active_chat"):
        assert classify_display(200, {**pending_body, "reason": reason}, None) == (
            "pending",
            reason,
        )
    assert classify_display(
        200, {**pending_body, "reason": "agent_id_mismatch"}, None
    ) == ("refused", "agent_id_mismatch")
    assert classify_display(
        200, {"ok": False, "reason": "debug_unreachable", "pasted": False}, None
    ) == ("pending", "debug_unreachable")
    for reason in ("signed_out", "ambiguous_title"):
        assert classify_display(200, {**pending_body, "reason": reason}, None) == (
            "refused",
            reason,
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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

    def _capture(
        row: dict, sub: dict, turn: int | str, *, text: str = ""
    ) -> tuple[str, str]:
        del text
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


def test_fire_due_wakes_pastes_one_line_per_thread(db: None) -> None:
    """Break: cross-thread tab coalesce must paste ordered multi-line BRIDGE_WAKE."""
    from unittest.mock import patch

    from agent_bus_store.db import create_thread, insert_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        advance_scan,
        claim_due_wakes,
        get_cursor,
        record_wake_result,
    )
    from cursor_bridge.chat_target import wake_lines

    tab = "paste-tab-22716"
    d1 = create_thread(thread_id=None, slug="paste-a")
    d2 = create_thread(thread_id=None, slug="paste-b")
    assert d1 and d2
    t1, _ts, n1, _c = insert_turn(
        thread=str(d1["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|a",
        body="a",
    )
    t2, _ts2, n2, _c2 = insert_turn(
        thread=str(d2["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|b",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t2,
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d1["id"]),
                "turn_id": t1,
                "turn_number": n1,
                "delivery_agent_id": tab,
            },
            {
                "agent": "cursor-sdk",
                "thread_id": str(d2["id"]),
                "turn_id": t2,
                "turn_number": n2,
                "delivery_agent_id": tab,
            },
        ],
        now="2019-12-31 23:59:50",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:10")
    assert claimed["wake_id"]
    captured: list[dict[str, object]] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            if agent == "cursor-sdk":
                return (
                    "exact",
                    {
                        "id": 9,
                        "surface": "cursor",
                        "display": "8",
                        "title": "Agent",
                        "agent_id": tab,
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

    def _capture(
        row: dict,
        sub: dict,
        turn: int | str,
        *,
        text: str = "",
        wake_groups: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str]:
        del row, sub, turn
        captured.append({"text": text, "wake_groups": wake_groups})
        return ("delivered", "match")

    expected_groups = [
        {"thread_id": str(d1["id"]), "turn": n1},
        {"thread_id": str(d2["id"]), "turn": n2},
    ]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert captured == [{"text": "", "wake_groups": expected_groups}]
    assert wake_lines(expected_groups) == (
        f"BRIDGE_WAKE thread={d1['id']} turn={n1}\n"
        f"BRIDGE_WAKE thread={d2['id']} turn={n2}"
    )
    with connect() as conn:
        states = [
            str(r["state"])
            for r in conn.execute(
                "SELECT state FROM endpoint_wake_ledger WHERE wake_id=?",
                (claimed["wake_id"],),
            )
        ]
    assert states == ["delivered", "delivered"]

    d3 = create_thread(thread_id=None, slug="paste-fail")
    assert d3
    t3, _ts3, n3, _c3 = insert_turn(
        thread=str(d3["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|c",
        body="c",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        t2,
        t3,
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d3["id"]),
                "turn_id": t3,
                "turn_number": n3,
                "delivery_agent_id": tab,
            },
        ],
        now="2020-01-01 00:20:00",
    )
    claimed2 = claim_due_wakes(now="2020-01-01 00:20:10")

    class Store2:
        def claim(self) -> dict:
            return claimed2

        def subscription(self, agent: str, thread_id: str):
            return Store().subscription(agent, thread_id)

        def result(self, **payload: object) -> int:
            return Store().result(**payload)

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=("refused", "title_mismatch"),
    ):
        fire_due_wakes(Store2())  # type: ignore[arg-type]
    with connect() as conn:
        fail_states = [
            str(r["state"])
            for r in conn.execute(
                "SELECT state FROM endpoint_wake_ledger WHERE wake_id=?",
                (claimed2["wake_id"],),
            )
        ]
    assert fail_states == ["refused"]


def test_fire_due_wakes_primary_is_tab_row(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db import create_thread, insert_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        advance_scan,
        claim_due_wakes,
        get_cursor,
        record_wake_result,
    )

    tab = "primary-tab-a"
    d1 = create_thread(thread_id=None, slug="prim-a")
    assert d1
    t_hi, _ts, n_hi, _c = insert_turn(
        thread=str(d1["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|null",
        body="b",
    )
    t_tab, _ts2, n_tab, _c2 = insert_turn(
        thread=str(d1["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|tab",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        max(t_hi, t_tab),
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d1["id"]),
                "turn_id": t_hi,
                "turn_number": n_hi,
            },
            {
                "agent": "cursor-sdk",
                "thread_id": str(d1["id"]),
                "turn_id": t_tab,
                "turn_number": n_tab,
                "delivery_agent_id": tab,
            },
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:05")
    subs: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            del agent, thread_id
            sub = {
                "id": 11,
                "surface": "cursor",
                "display": "8",
                "title": "Agent",
                "agent_id": tab,
            }
            subs.append(sub)
            return ("exact", sub)

        def result(self, **payload: object) -> int:
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
    assert subs and subs[0]["agent_id"] == tab


def test_fire_due_wakes_orders_threads_by_turn_id(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db import create_thread, insert_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        advance_scan,
        claim_due_wakes,
        get_cursor,
        record_wake_result,
    )

    tab = "order-tab"
    d_lo = create_thread(thread_id=None, slug="order-lo")
    d_hi = create_thread(thread_id=None, slug="order-hi")
    assert d_lo and d_hi
    t_lo, _a, n_lo, _ = insert_turn(
        thread=str(d_lo["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|lo",
        body="a",
    )
    t_hi, _b, n_hi, _ = insert_turn(
        thread=str(d_hi["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|hi",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        max(t_lo, t_hi),
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d_lo["id"]),
                "turn_id": t_lo,
                "turn_number": n_lo,
                "delivery_agent_id": tab,
            },
            {
                "agent": "cursor-sdk",
                "thread_id": str(d_hi["id"]),
                "turn_id": t_hi,
                "turn_number": n_hi,
                "delivery_agent_id": tab,
            },
        ],
        now="2019-12-31 23:59:50",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:10")
    groups_seen: list[list[dict[str, Any]]] = []
    primary_threads: list[str] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            return (
                "exact",
                {
                    "id": 3,
                    "surface": "cursor",
                    "display": "8",
                    "title": "A",
                    "agent_id": tab,
                },
            )

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                "delivered",
                "match",
                "exact",
                3,
                None,
            )

    def _capture(
        row: dict,
        sub: dict,
        turn: int | str,
        *,
        text: str = "",
        wake_groups: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str]:
        del sub, turn, text
        primary_threads.append(str(row["thread_id"]))
        groups_seen.append(list(wake_groups or []))
        return ("delivered", "match")

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert groups_seen[0][0]["thread_id"] == str(d_lo["id"])
    assert groups_seen[0][1]["thread_id"] == str(d_hi["id"])
    assert primary_threads == [str(d_hi["id"])]


def test_fire_due_wakes_multi_thread_real_display_path(db: None) -> None:
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent_bus_store.db import create_thread, insert_turn
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        advance_scan,
        claim_due_wakes,
        get_cursor,
        record_wake_result,
    )
    from cursor_bridge.chat_target import wake_line, wake_lines

    from pipelines.display_chat_wake.v1.handlers.wake import DisplayChatWakeHandler

    tab = "real-path-tab"
    d1 = create_thread(thread_id=None, slug="real-a")
    d2 = create_thread(thread_id=None, slug="real-b")
    assert d1 and d2
    t1, _ts, n1, _ = insert_turn(
        thread=str(d1["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|a",
        body="a",
    )
    t2, _ts2, n2, _ = insert_turn(
        thread=str(d2["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|b",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t2,
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d1["id"]),
                "turn_id": t1,
                "turn_number": n1,
                "delivery_agent_id": tab,
            },
            {
                "agent": "cursor-sdk",
                "thread_id": str(d2["id"]),
                "turn_id": t2,
                "turn_number": n2,
                "delivery_agent_id": tab,
            },
        ],
        now="2019-12-31 23:59:50",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:10")
    lines_captured: list[str] = []

    class Store:
        def claim(self) -> dict:
            return claimed

        def subscription(self, agent: str, thread_id: str):
            return (
                "exact",
                {
                    "id": 7,
                    "surface": "cursor",
                    "display": "8",
                    "title": "A",
                    "agent_id": tab,
                },
            )

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                "exact",
                7,
                None,
            )

    async def _fake_deliver_locked(*, line: str, **_: object) -> dict:
        lines_captured.append(line)
        return {"ok": True, "delivered": True, "pasted": True, "reason": "match"}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"ok": True, "delivered": True})
                        }
                    }
                ]
            }

    class _FakeClient:
        def post(self, *_a: object, **kwargs: object) -> _FakeResponse:
            opts = (kwargs.get("json") or {})["pipeline_options"]  # type: ignore[index]
            handler = DisplayChatWakeHandler()
            ctx = MagicMock()
            ctx.options = opts
            out = asyncio.run(handler.execute(MagicMock(), ctx))
            payload = json.loads(out.raw)
            if payload.get("reason") == "exchange_paste_cursor_stage_manager_only":
                return _FakeResponse()
            return _FakeResponse()

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    expected = wake_lines(
        [
            {"thread_id": str(d1["id"]), "turn": n1},
            {"thread_id": str(d2["id"]), "turn": n2},
        ]
    )
    with (
        patch(
            "bus_watch.endpoint_wake_dispatcher.make_sync_client",
            return_value=_FakeClient(),
        ),
        patch(
            "cursor_bridge.wake_delivery._deliver_locked",
            new=AsyncMock(side_effect=_fake_deliver_locked),
        ),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    assert lines_captured == [expected]
    with connect() as conn:
        reasons = [
            str(r["reason"] or "")
            for r in conn.execute(
                "SELECT reason FROM endpoint_wake_ledger WHERE wake_id=?",
                (claimed["wake_id"],),
            )
        ]
    assert all(r != "exchange_paste_cursor_stage_manager_only" for r in reasons)

    d3 = create_thread(thread_id=None, slug="real-single")
    assert d3
    t3, _ts3, n3, _ = insert_turn(
        thread=str(d3["id"]),
        from_agent="cursor",
        to_agent="cursor-sdk",
        subject="FACT|c",
        body="c",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        t2,
        t3,
        [
            {
                "agent": "cursor-sdk",
                "thread_id": str(d3["id"]),
                "turn_id": t3,
                "turn_number": n3,
                "delivery_agent_id": tab,
            },
        ],
        now="2020-01-01 00:10:00",
    )
    claimed_one = claim_due_wakes(now="2020-01-01 00:10:05")
    posts: list[dict] = []

    class StoreOne:
        def claim(self) -> dict:
            return claimed_one

        def subscription(self, agent: str, thread_id: str):
            return Store().subscription(agent, thread_id)

        def result(self, **payload: object) -> int:
            return Store().result(**payload)

    class _OptsClient:
        def post(self, *_a: object, **kwargs: object) -> _FakeResponse:
            posts.append((kwargs.get("json") or {})["pipeline_options"])
            lines_captured.append(wake_line(str(d3["id"]), n3))
            return _FakeResponse()

        def __enter__(self) -> _OptsClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    with (
        patch(
            "bus_watch.endpoint_wake_dispatcher.make_sync_client",
            return_value=_OptsClient(),
        ),
        patch(
            "cursor_bridge.wake_delivery._deliver_locked",
            new=AsyncMock(side_effect=_fake_deliver_locked),
        ),
    ):
        fire_due_wakes(StoreOne())  # type: ignore[arg-type]
    assert "wake_groups" not in posts[0]
    assert "text" not in posts[0]
    assert lines_captured[-1] == wake_line(str(d3["id"]), n3)


def test_pending_item_omits_delivery_agent_id_off_display_surfaces() -> None:
    from bus_watch.endpoint_wake_dispatcher import _pending_item

    item = _pending_item(
        {"thread": "1", "id": 1, "turn_number": 1, "to_agent": "orion", "subject": ""},
        decided_by_rule="r",
        route=(
            "exact",
            {
                "display": "8",
                "agent_id": "uuid-chatgpt",
                "surface": "chatgpt",
            },
        ),
    )
    assert "delivery_agent_id" not in item


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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
    """18460#176: no_composer pending; first strike under grace keeps sub; second drops."""
    _reset_no_composer_state()
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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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

    display_ret = classify_display(200, wake_out, None)

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=display_ret,
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
    assert ledger_row["route"] == "remapped"
    assert ledger_row["subscription_id"] == sub_row["id"]
    assert ledger_row["finished_at"] == "2026-10-20 22:59:57"

    assert len(unsub_calls) == 0
    assert resolve_endpoint("cursor", thread) is not None
    cur_sub = current_subscription("cursor", thread)
    assert cur_sub is not None
    assert cur_sub["state"] == "subscribed"

    turn2_id, _ts2, number2, _c2 = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="REQUEST|freeform|follow-up",
        body="TYPE: REQUEST\nBody2",
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        turn_id,
        turn2_id,
        [
            {
                "agent": "cursor",
                "thread_id": thread,
                "turn_id": turn2_id,
                "turn_number": number2,
            }
        ],
        now="2026-10-20 23:00:00",
    )
    claimed2 = claim_due_wakes(now="2026-10-20 23:00:01")
    assert claimed2["wake_id"]

    class Store2:
        def claim(self) -> dict:
            return claimed2

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
                now="2026-10-20 23:00:01",
            )

    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=display_ret,
    ):
        fire_due_wakes(Store2())  # type: ignore[arg-type]

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
    # RULING a:41381 option (ii): the row stays, the departed tab is unbound.
    assert resolve_endpoint("cursor", thread) is not None
    cur_sub = current_subscription("cursor", thread)
    assert cur_sub is not None
    assert cur_sub["state"] == "subscribed"
    assert cur_sub["agent_id"] is None
    assert str(cur_sub.get("evidence") or "") == f"hop_expired_unbound:{dead_agent_id}"

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


def _cursor_no_composer_wake_fixture(
    *,
    agent_id: str = "agent-dead-001",
    scan_now: str = "2026-10-21 12:00:00",
    claim_now: str = "2026-10-21 12:00:01",
) -> tuple[str, dict, dict]:
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    detail = create_thread(thread_id=None, slug="no-composer-threshold")
    assert detail is not None
    thread = str(detail["id"])
    title = f"{thread} no-composer-threshold"
    sub_res = subscribe_endpoint(
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=title,
        agent_id=agent_id,
        bound_by="test",
    )
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="REQUEST|sync",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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
        now=scan_now,
    )
    claimed = claim_due_wakes(now=claim_now)
    assert claimed["wake_id"]
    return thread, claimed, sub_res["row"]


def _make_display_wake_store(
    claimed: dict,
    unsub_calls: list[tuple[str, str]],
    *,
    result_now: str,
) -> object:
    from agent_bus_store.db.endpoint_subscriptions import (
        resolve_endpoint,
        unsubscribe_endpoint,
    )

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
                now=result_now,
            )

    return Store()


def test_first_no_composer_under_grace_does_not_unsubscribe(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        resolve_endpoint,
    )

    from bus_watch import endpoint_wake_dispatcher as ewd

    _reset_no_composer_state()
    thread, claimed, _sub = _cursor_no_composer_wake_fixture()
    unsub_calls: list[tuple[str, str]] = []
    mono = {"t": 5000.0}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ewd.time, "monotonic", lambda: mono["t"])
    try:
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed, unsub_calls, result_now="2026-10-21 12:00:01"
                )
            )  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()
    assert unsub_calls == []
    assert resolve_endpoint("cursor", thread) is not None
    cur = current_subscription("cursor", thread)
    assert cur is not None
    assert cur["state"] == "subscribed"


def test_second_consecutive_no_composer_unsubscribes(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        resolve_endpoint,
    )

    from bus_watch import endpoint_wake_dispatcher as ewd

    _reset_no_composer_state()
    thread, claimed, _sub = _cursor_no_composer_wake_fixture()
    unsub_calls: list[tuple[str, str]] = []
    mono = {"t": 6000.0}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ewd.time, "monotonic", lambda: mono["t"])
    try:
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed, unsub_calls, result_now="2026-10-21 12:00:01"
                )
            )  # type: ignore[arg-type]
        assert unsub_calls == []
        turn2_id, _ts2, number2, _c2 = insert_turn(
            thread=thread,
            from_agent="maestro",
            to_agent="cursor",
            subject="REQUEST|again",
            body="b2",
        )
        advance_scan(
            "endpoint-wake-dispatcher",
            int(claimed["rows"][0]["turn_id"]),
            turn2_id,
            [
                {
                    "agent": "cursor",
                    "thread_id": thread,
                    "turn_id": turn2_id,
                    "turn_number": number2,
                }
            ],
            now="2026-10-21 12:00:10",
        )
        claimed2 = claim_due_wakes(now="2026-10-21 12:00:11")
        mono["t"] = 6001.0
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed2, unsub_calls, result_now="2026-10-21 12:00:11"
                )
            )  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()
    assert len(unsub_calls) == 1
    assert resolve_endpoint("cursor", thread) is None
    cur = current_subscription("cursor", thread)
    assert cur is not None
    assert cur["state"] == "unsubscribed"


def test_delivered_resets_no_composer_streak(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db.endpoint_subscriptions import resolve_endpoint

    from bus_watch import endpoint_wake_dispatcher as ewd

    _reset_no_composer_state()
    thread, claimed, _sub = _cursor_no_composer_wake_fixture()
    unsub_calls: list[tuple[str, str]] = []
    mono = {"t": 8000.0}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ewd.time, "monotonic", lambda: mono["t"])
    try:
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed, unsub_calls, result_now="2026-10-21 12:00:01"
                )
            )  # type: ignore[arg-type]
        turn2_id, _ts2, number2, _c2 = insert_turn(
            thread=thread,
            from_agent="maestro",
            to_agent="cursor",
            subject="REQUEST|delivered",
            body="b2",
        )
        advance_scan(
            "endpoint-wake-dispatcher",
            int(claimed["rows"][0]["turn_id"]),
            turn2_id,
            [
                {
                    "agent": "cursor",
                    "thread_id": thread,
                    "turn_id": turn2_id,
                    "turn_number": number2,
                }
            ],
            now="2026-10-21 12:00:05",
        )
        claimed2 = claim_due_wakes(now="2026-10-21 12:00:06")
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("delivered", "delivered"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed2, unsub_calls, result_now="2026-10-21 12:00:06"
                )
            )  # type: ignore[arg-type]
        turn3_id, _ts3, number3, _c3 = insert_turn(
            thread=thread,
            from_agent="maestro",
            to_agent="cursor",
            subject="REQUEST|after-delivered",
            body="b3",
        )
        advance_scan(
            "endpoint-wake-dispatcher",
            turn2_id,
            turn3_id,
            [
                {
                    "agent": "cursor",
                    "thread_id": thread,
                    "turn_id": turn3_id,
                    "turn_number": number3,
                }
            ],
            now="2026-10-21 12:00:10",
        )
        claimed3 = claim_due_wakes(now="2026-10-21 12:00:11")
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed3, unsub_calls, result_now="2026-10-21 12:00:11"
                )
            )  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()
    assert unsub_calls == []
    assert resolve_endpoint("cursor", thread) is not None


def test_no_composer_after_uptime_grace_unsubscribes(db: None) -> None:
    from unittest.mock import patch

    from agent_bus_store.db.endpoint_subscriptions import current_subscription

    from bus_watch import endpoint_wake_dispatcher as ewd

    _reset_no_composer_state()
    thread, claimed, _sub = _cursor_no_composer_wake_fixture()
    unsub_calls: list[tuple[str, str]] = []
    mono = {"t": 7000.0}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ewd.time, "monotonic", lambda: mono["t"])
    try:
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "unknown"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed, unsub_calls, result_now="2026-10-21 12:00:01"
                )
            )  # type: ignore[arg-type]
        assert unsub_calls == []
        turn2_id, _ts2, number2, _c2 = insert_turn(
            thread=thread,
            from_agent="maestro",
            to_agent="cursor",
            subject="REQUEST|grace",
            body="b2",
        )
        advance_scan(
            "endpoint-wake-dispatcher",
            int(claimed["rows"][0]["turn_id"]),
            turn2_id,
            [
                {
                    "agent": "cursor",
                    "thread_id": thread,
                    "turn_id": turn2_id,
                    "turn_number": number2,
                }
            ],
            now="2026-10-21 12:00:30",
        )
        claimed2 = claim_due_wakes(now="2026-10-21 12:00:31")
        mono["t"] = 7031.0
        with patch(
            "bus_watch.endpoint_wake_dispatcher._display_post",
            return_value=("pending", "no_composer"),
        ):
            fire_due_wakes(
                _make_display_wake_store(
                    claimed2, unsub_calls, result_now="2026-10-21 12:00:31"
                )
            )  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()
    assert len(unsub_calls) == 1
    cur = current_subscription("cursor", thread)
    assert cur is not None
    assert cur["state"] == "unsubscribed"


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
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
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


def test_declared_rule_map_loads_address_rules(db: None) -> None:
    from bus_watch.endpoint_wake_dispatcher import _FALLBACK_WAKE_RULE_IDS

    declared = _declared_wake_rule_ids()
    assert declared == _FALLBACK_WAKE_RULE_IDS
    assert len(declared) == len(_FALLBACK_WAKE_RULE_IDS)
    assert declared[1] == "drop_orion_echo"
    assert declared[6] == "wake_by_address"
    assert declared[7] == "wake_by_name"
    assert declared[8] == "refuse_address_mismatch"
    assert declared[14] == "drop_routine_hop_closeout"
    assert declared[13] == "wake_peer_sender"
    assert declared[-1] == "default_suppress"


def test_cursor_sdk_closeout_to_non_cursor_cse_wakes_gate() -> None:
    """Phase 1a: worker CLOSEOUT wakes addressed non-cursor seats when cse=True."""
    verdict = qualify(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk CLOSEOUT a1b2c3d4 | giw pointer",
            "thread": "22794",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_gate_or_directive"


def test_cdp_reply_outside_peer_set_cse_wakes_gate() -> None:
    verdict = qualify(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "external-relay",
            "subject": "cdp reply — 8fec4c00",
            "thread": "22794",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_gate_or_directive"


def test_closeout_subject_with_intermediate_token_still_drops() -> None:
    verdict = qualify(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor-sdk",
            "subject": "cursor-sdk CLOSEOUT a1b2 | admitted leg",
            "thread": "22794",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "drop_dispatch_intermediate"


def test_routine_conductor_hop_closeout_still_drops() -> None:
    verdict = qualify(
        {
            "to_agent": "grok-bot-gc1",
            "from_agent": "cursor-sdk",
            "subject": (
                "cursor-sdk CLOSEOUT 2d1f2c04d3ff-15c30a1d "
                "contract=conductor caller=conductor-hop"
            ),
            "thread": "22794",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "drop_routine_hop_closeout"


@pytest.mark.parametrize(
    "subject",
    (
        "Quiet with work in flight",
        "PARKED | lane 22794",
        "HOLD_MERGE | lane 22794",
        "OPERATOR_GATE | lane 22794",
    ),
)
def test_non_closeout_gate_subjects_cse_random_seat_suppressed(subject: str) -> None:
    """CSE Cowork seats must not wake on non-CLOSEOUT gate vocabulary."""
    verdict = qualify(
        {
            "to_agent": "random-seat",
            "from_agent": "dispatch",
            "subject": subject,
            "thread": "22794",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "default_suppress"


def test_qualify_matches_legacy_over_qualification_matrix(db: None) -> None:
    """YAML-backed qualify() must match imperative _legacy_qualifies on every case."""
    agents = (
        "maestro",
        "web-anthropic",
        "grok-bot-gc1",
        "cursor",
        "cursor-sdk",
        "conductor-hop",
        "dispatch",
        "all",
        "",
    )
    subjects = (
        "QUESTION|x",
        "VERDICT|x",
        "FACT|telemetry",
        "ACK|x",
        "ROUTE|x",
        "STATUS|ack-batch|x",
        "TYPE: DIRECTIVE|x",
        "NOTICE|gap|sha",
        "pickup|house-cp",
        "CLOSEOUT|cursor-sdk generate",
        "contract=conductor caller=conductor-hop",
        "DONE|x",
        "RANDOM|noise",
    )
    mismatches: list[str] = []
    for to_agent in agents:
        for from_agent in agents:
            for subject in subjects:
                turn = {
                    "to_agent": to_agent,
                    "from_agent": from_agent,
                    "subject": subject,
                }
                for cse in (True, False):
                    legacy = _legacy_qualifies(turn, cse=cse)
                    mapped = qualify(turn, cse=cse).wake
                    if legacy != mapped:
                        mismatches.append(
                            f"cse={cse} to={to_agent!r} from={from_agent!r} "
                            f"subject={subject!r} legacy={legacy} mapped={mapped}"
                        )
    assert mismatches == []


def test_advance_scan_persists_decided_by_rule(db: None) -> None:
    detail = create_thread(thread_id=None, slug="40226-decided-by-rule")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|decided-by-rule",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
                "decided_by_rule": "wake_peer_sender",
            }
        ],
        now="2020-01-01 19:00:00",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert row is not None
    assert row["decided_by_rule"] == "wake_peer_sender"


def test_scan_once_does_not_enqueue_already_read_turn(db: None) -> None:
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="40226-scan-read")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|40226",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 18:00:00", t_id),
        )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    scanned: list[dict] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, pending: list[dict]) -> dict:
            scanned.extend(pending)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                pending,
                now="2020-01-01 18:00:01",
            )

        def holder(self, lane: str) -> dict:
            return {"registration_id": "reg-live", "seat_state": "driving"}

        def subscription(self, agent: str, thread_id: str):
            return (
                "cse",
                {
                    "surface": "cse",
                    "agent_id": thread_id,
                    "id": 1,
                    "state": "subscribed",
                },
            )

    scan_once(Store())  # type: ignore[arg-type]
    assert scanned == []
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?", (t_id,)
        ).fetchone()
        drop = conn.execute(
            "SELECT reason FROM endpoint_wake_scan_drops WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert row is None
    assert drop is not None
    assert drop["reason"] == "skipped:already_read"


def test_claim_skips_maestro_already_read_after_pending_insert(db: None) -> None:
    detail = create_thread(thread_id=None, slug="40226-maestro-read-claim")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|40226",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 18:05:00",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 18:05:30", t_id),
        )
    skipped = claim_due_wakes(now="2020-01-01 18:06:00")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["reason"] == "already_read"


def test_claim_skips_when_owed_wake_dispatched_same_turn(db: None, tmp_path) -> None:
    import scripts.model_manager.ui.controller.restart_intent_store as ris_mod

    intent_db = tmp_path / "restart-intents.db"
    ris_mod.RestartIntentStore._instance = None
    store = ris_mod.RestartIntentStore(db_path=intent_db)
    ris_mod.RestartIntentStore._instance = store
    detail = create_thread(thread_id=None, slug="40226-dedupe-owed")
    assert detail is not None
    thread = str(detail["id"])
    t_id, _ts, t_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|40226",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        t_id,
        [
            {
                "agent": "maestro",
                "thread_id": thread,
                "turn_id": t_id,
                "turn_number": t_n,
            }
        ],
        now="2020-01-01 18:10:00",
    )
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO owed_wake (row_id, holder_id, producer, lane_thread_id, "
            "turn_refs, arming_lanes, dispatched_at, dispatch_outcome, created_at, "
            "updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "row-40226",
                "holder-maestro",
                "milestone",
                thread,
                f'[{{"thread_id":"{thread}","turn_number":{t_n}}}]',
                "[]",
                "2020-01-01 18:10:05",
                "dispatched",
                "2020-01-01 18:10:01",
                "2020-01-01 18:10:05",
            ),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS c FROM owed_wake").fetchone()["c"] == 1
    skipped = claim_due_wakes(now="2020-01-01 18:10:10")
    assert skipped["wake_id"] is None
    assert skipped["rows"][0]["reason"] == "deduped_owed_wake"


def test_fire_due_wakes_skips_when_cse_holder_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

    recorded: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-1",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "maestro",
                        "thread_id": "18460",
                        "turn_id": 1,
                        "turn_number": 2,
                        "decided_by_rule": "wake_cse_allowlist",
                    }
                ],
            }

        def holder(self, _lane: str) -> dict:
            return {"seat_state": "superseded", "registration_id": "reg-old"}

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_target",
        lambda _store, _agent, _thread: (
            "exact",
            {"surface": "cse", "agent_id": "18460", "id": 9},
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: recorded.append(kw),
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert recorded[0]["outcome"] == "skipped"
    assert recorded[0]["reason"] == "seat_superseded"
    assert recorded[0]["decided_by_rule"] == "wake_cse_allowlist"


def test_fire_due_wakes_skips_web_anthropic_when_cse_holder_superseded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

    recorded: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-web-1",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "web-anthropic",
                        "thread_id": "40226",
                        "turn_id": 1,
                        "turn_number": 2,
                        "decided_by_rule": "wake_web_anthropic",
                    }
                ],
            }

        def holder(self, _lane: str) -> dict:
            return {"seat_state": "superseded", "registration_id": "reg-web-old"}

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._display_seated",
        lambda _store, _thread: False,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: recorded.append(kw),
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert recorded[0]["outcome"] == "skipped"
    assert recorded[0]["reason"] == "seat_superseded"
    assert recorded[0]["route"] == "cse"


_SPECIMEN_CLOSEOUT = (
    "cursor-sdk CLOSEOUT d5b2dad0952d-a5b4f1cf contract=implement caller=web-anthropic"
)


class _LaneChainStore:
    """Holder rows live on the driving lane; worker threads only have a parent."""

    def __init__(
        self,
        holders: dict[str, dict[str, str] | None],
        parents: dict[str, str | None],
    ) -> None:
        self.holders = holders
        self.parents = parents

    def holder(self, lane: str) -> dict[str, str] | None:
        return self.holders.get(lane)

    def lane_parent(self, thread: str) -> str | None:
        return self.parents.get(thread)


def _specimen_turn(*, to_agent: str, thread: str = "20714") -> dict[str, object]:
    return {
        "to_agent": to_agent,
        "from_agent": "cursor-sdk",
        "subject": _SPECIMEN_CLOSEOUT,
        "thread": thread,
        "id": 6,
        "turn_number": 6,
    }


def test_worker_closeout_to_web_anthropic_wakes_lane_holder() -> None:
    """20714#6 shape: to=web-anthropic, old subject, parent lane held by R."""
    from bus_watch.endpoint_wake_dispatcher import resolve_turn_address

    store = _LaneChainStore(
        holders={
            "12286": {
                "registration_id": "R",
                "chat_url": "https://claude.ai/chat/R",
                "seat_state": "driving",
            }
        },
        parents={"20714": "12286"},
    )
    verdict = qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=store,  # type: ignore[arg-type]
        seat_registration="R",
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_by_address"
    address = resolve_turn_address("20714", store)  # type: ignore[arg-type]
    assert address is not None
    assert address.registration_id == "R"
    assert address.lane_thread == "12286"


def test_worker_closeout_does_not_wake_a_different_registration() -> None:
    store = _LaneChainStore(
        holders={"12286": {"registration_id": "S", "seat_state": "driving"}},
        parents={"20714": "12286"},
    )
    verdict = qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=store,  # type: ignore[arg-type]
        seat_registration="R",
    )
    assert verdict.wake is False
    assert verdict.rule_id == "refuse_address_mismatch"
    other = qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=store,  # type: ignore[arg-type]
        seat_registration="S",
    )
    assert other.wake is True
    assert other.rule_id == "wake_by_address"


def test_closeout_without_lane_holder_falls_back_to_alias_names() -> None:
    store = _LaneChainStore(holders={}, parents={"20714": None})
    for to_agent in ("web-anthropic", "maestro", "claude-web"):
        verdict = qualify(
            _specimen_turn(to_agent=to_agent),
            cse=True,
            store=store,  # type: ignore[arg-type]
        )
        assert verdict.wake is True, to_agent
        assert verdict.rule_id == "wake_by_name", to_agent
    outsider = qualify(
        _specimen_turn(to_agent="random-seat"),
        cse=True,
        store=store,  # type: ignore[arg-type]
    )
    assert outsider.wake is True
    assert outsider.rule_id == "wake_gate_or_directive"
    cursor = qualify(
        _specimen_turn(to_agent="cursor"),
        cse=False,
        store=store,  # type: ignore[arg-type]
    )
    assert cursor.wake is True
    assert cursor.rule_id == "wake_gate_or_directive"


def test_prefixed_closeout_subject_wakes_same_holder() -> None:
    store = _LaneChainStore(
        holders={"12286": {"registration_id": "R", "seat_state": "driving"}},
        parents={"20714": "12286"},
    )
    turn = _specimen_turn(to_agent="web-anthropic")
    turn["subject"] = "CLOSEOUT|" + _SPECIMEN_CLOSEOUT
    verdict = qualify(
        turn,
        cse=True,
        store=store,  # type: ignore[arg-type]
        seat_registration="R",
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_by_address"


@pytest.mark.offline
def test_endpoint_wake_address_route_reaches_agent_bus_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qualify path must publish via agent-bus UDS publisher, not a silent no-op."""
    import json

    from agent_bus_store.events import publisher
    from agent_bus_store.server import wire_bus_watch_event_publisher

    queued: list[dict[str, object]] = []

    def _capture_put(self, line: str) -> None:
        queued.append(json.loads(line.strip()))

    monkeypatch.setattr(publisher._UDSPublisher, "put_nowait", _capture_put)
    wire_bus_watch_event_publisher()
    held = _LaneChainStore(
        holders={"12286": {"registration_id": "R", "seat_state": "driving"}},
        parents={"20714": "12286"},
    )
    qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=held,  # type: ignore[arg-type]
        seat_registration="R",
    )
    signals = [str(e.get("signal")) for e in queued]
    assert "endpoint_wake.address_route" in signals
    evt = next(e for e in queued if e.get("signal") == "endpoint_wake.address_route")
    assert evt.get("role") == "observation"
    payload = evt.get("payload")
    assert isinstance(payload, dict)
    assert payload.get("match") == "address"


def test_address_route_emits_match_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_address_route",
        lambda **kwargs: seen.append(kwargs),
    )
    held = _LaneChainStore(
        holders={"12286": {"registration_id": "R", "seat_state": "driving"}},
        parents={"20714": "12286"},
    )
    qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=held,  # type: ignore[arg-type]
        seat_registration="R",
    )
    assert seen[-1]["match"] == "address"
    qualify(
        _specimen_turn(to_agent="web-anthropic"),
        cse=True,
        store=held,  # type: ignore[arg-type]
        seat_registration="other",
    )
    assert seen[-1]["match"] == "refused"
    bare = _LaneChainStore(holders={}, parents={})
    qualify(
        _specimen_turn(to_agent="claude-web"),
        cse=True,
        store=bare,  # type: ignore[arg-type]
    )
    assert seen[-1]["match"] == "name"


def test_scan_once_enqueues_worker_closeout_for_parent_holder(db: None) -> None:
    detail = create_thread(thread_id=None, slug="40330-worker")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="web-anthropic",
        subject=_SPECIMEN_CLOSEOUT,
        body="closeout",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    pending: list[dict] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            pending.extend(rows)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def holder(self, lane: str) -> dict | None:
            if lane == "12286":
                return {"registration_id": "R", "seat_state": "driving"}
            return None

        def lane_parent(self, lane: str) -> str | None:
            if lane == thread:
                return "12286"
            return None

        def subscription(self, agent: str, thread_id: str):
            return None

    scan_once(Store())  # type: ignore[arg-type]
    assert len(pending) == 1
    assert pending[0]["agent"] == "web-anthropic"
    assert pending[0]["thread_id"] == thread
    assert pending[0]["turn_id"] == turn_id
    assert pending[0]["turn_number"] == number
    assert pending[0]["decided_by_rule"] == "wake_by_address"


def test_fire_due_wakes_delivers_worker_closeout_to_parent_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[tuple[str, str]] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-40330",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "web-anthropic",
                        "thread_id": "20714",
                        "turn_id": 6,
                        "turn_number": 6,
                        "decided_by_rule": "wake_by_address",
                    }
                ],
            }

        def holder(self, lane: str) -> dict | None:
            if lane == "12286":
                return {
                    "registration_id": "R",
                    "seat_state": "driving",
                    "chat_url": "https://claude.ai/chat/R",
                }
            return None

        def lane_parent(self, thread: str) -> str | None:
            if thread == "20714":
                return "12286"
            return None

        def subscription(self, agent: str, thread_id: str):
            return None

        def result(self, **payload: object) -> dict:
            return payload

    def _deliver(rows, holder, target_lane=None):
        delivered.append((str(target_lane), str(holder.get("registration_id"))))
        return "delivered", "send_verified=True", "R"

    monkeypatch.setattr("bus_watch.endpoint_wake_dispatcher.deliver", _deliver)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: None,
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert delivered == [("12286", "R")]


_GLASS_IMPRESARIO_AGENT_ID = "f907a88b-6310-4b66-9de0-edd12be89898"
_GLASS_IMPRESARIO_SUBSCRIPTION = {
    "id": 42,
    "agent": "cursor",
    "thread_scope": "20995",
    "state": "subscribed",
    "surface": "cursor",
    "display": "8",
    "title": "20995 impresario",
    "agent_id": _GLASS_IMPRESARIO_AGENT_ID,
}
_ORION_AGENT_ID = "4ed020f2-f0b2-4f3d-b49c-89aa34930f19"
_ORION_SUBSCRIPTION = {
    "id": 43,
    "agent": "cursor",
    "thread_scope": "20995",
    "state": "subscribed",
    "surface": "cursor",
    "display": "orion-node",
    "title": "20995 impresario",
    "agent_id": _ORION_AGENT_ID,
}


def test_worker_closeout_to_composer_impresario_resolves_glass_binding() -> None:
    """Worker CLOSEOUT to composer-impresario-<id> wakes the Glass :8 row by agent_id."""
    from bus_watch.endpoint_wake_dispatcher import _resolve_delivery_route, qualify
    from bus_watch.endpoint_wake_transport import StoreClient

    worker_thread = "21235"
    to_agent = "composer-impresario-f907a88b"
    subject = "cursor-sdk CLOSEOUT d5b2 contract=implement"
    turn = {
        "to_agent": to_agent,
        "from_agent": "cursor-sdk",
        "subject": subject,
        "thread": worker_thread,
        "id": 9,
        "turn_number": 3,
    }
    verdict = qualify(turn, cse=False, store=None)
    assert verdict.wake is True
    assert verdict.rule_id == "wake_gate_or_directive"

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> dict:
            if method == "GET" and path == "/endpoint-subscriptions":
                agent = (kwargs.get("params") or {}).get("agent")
                if agent == "cursor":
                    return {"subscriptions": [dict(_GLASS_IMPRESARIO_SUBSCRIPTION)]}
                return {"subscriptions": []}
            if (
                method == "GET"
                and path.startswith("/threads/")
                and path.endswith("/lane-current")
            ):
                return {"parent_thread": "20995"}
            raise AssertionError(f"unexpected bus call {method} {path}")

    stub = StubStore()
    resolved = stub.subscription(to_agent, worker_thread, raw_to=to_agent)
    assert resolved is not None
    kind, row = resolved
    assert kind == "parent"
    assert row["display"] == "8"
    assert row["agent_id"] == _GLASS_IMPRESARIO_AGENT_ID

    route = _resolve_delivery_route(stub, to_agent, worker_thread, raw_to=to_agent)
    assert route is not None
    assert route[1]["display"] == "8"
    assert route[1]["agent_id"] == _GLASS_IMPRESARIO_AGENT_ID


def test_worker_closeout_stale_uuid_to_resolves_glass_binding() -> None:
    """Mis-addressed to=<uuid-prefix> still binds the Glass row on the worker thread."""
    from bus_watch.endpoint_wake_dispatcher import _resolve_delivery_route

    worker_thread = "21228"
    raw_to = "4ed020f2"

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> dict:
            if method == "GET" and path == "/endpoint-subscriptions":
                if (kwargs.get("params") or {}).get("agent") == "cursor":
                    return {"subscriptions": [dict(_ORION_SUBSCRIPTION)]}
                return {"subscriptions": []}
            if (
                method == "GET"
                and path.startswith("/threads/")
                and path.endswith("/lane-current")
            ):
                return {"parent_thread": None}
            raise AssertionError(f"unexpected bus call {method} {path}")

    route = _resolve_delivery_route(StubStore(), raw_to, worker_thread, raw_to=raw_to)
    assert route is not None
    assert route[0] == "agent_id"
    assert route[1]["agent_id"] == _ORION_AGENT_ID


def test_scan_once_enqueues_composer_impresario_worker_closeout() -> None:
    worker_thread = "21235"
    to_agent = "composer-impresario-f907a88b"
    subject = "CLOSEOUT|cursor-sdk CLOSEOUT lane-21235 contract=implement"

    class Store(StoreClient):
        def __init__(self) -> None:
            super().__init__()
            self.pending: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, _high: int) -> list:
            return [
                {
                    "id": 77,
                    "to_agent": to_agent,
                    "from_agent": "cursor-sdk",
                    "thread": worker_thread,
                    "turn_number": 4,
                    "subject": subject,
                }
            ]

        def scan(self, _high: int, new_hw: int, rows: list) -> None:
            self.pending = rows

        def _bus(self, method: str, path: str, **kwargs: object) -> dict:
            if method == "GET" and path == "/endpoint-subscriptions":
                agent = (kwargs.get("params") or {}).get("agent")
                if agent == "cursor":
                    return {"subscriptions": [dict(_GLASS_IMPRESARIO_SUBSCRIPTION)]}
                return {"subscriptions": []}
            if (
                method == "GET"
                and path.startswith("/threads/")
                and path.endswith("/lane-current")
            ):
                tid = path.split("/")[2]
                if tid == worker_thread:
                    return {"parent_thread": "20995"}
                return {"parent_thread": None}
            raise AssertionError(f"unexpected bus call {method} {path}")

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert len(store.pending) == 1
    assert store.pending[0]["agent"] == to_agent
    assert store.pending[0]["thread_id"] == worker_thread
    assert store.pending[0]["decided_by_rule"] == "wake_gate_or_directive"
    assert store.pending[0]["display"] == "8"
    assert store.pending[0]["delivery_agent_id"] == _GLASS_IMPRESARIO_AGENT_ID


def test_scan_once_enqueues_dispatch_to_worker_closeout_on_house_scope() -> None:
    """21221#2 shape: to=dispatch on worker thread resolves house Glass subscription."""
    worker_thread = "21221"
    subject = "CLOSEOUT|cursor-sdk CLOSEOUT lane-21221 contract=implement"

    class Store(StoreClient):
        def __init__(self) -> None:
            super().__init__()
            self.pending: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, _high: int) -> list:
            return [
                {
                    "id": 88,
                    "to_agent": "dispatch",
                    "from_agent": "cursor-sdk",
                    "thread": worker_thread,
                    "turn_number": 2,
                    "subject": subject,
                }
            ]

        def scan(self, _high: int, new_hw: int, rows: list) -> None:
            self.pending = rows

        def _bus(self, method: str, path: str, **kwargs: object) -> dict:
            if method == "GET" and path == "/endpoint-subscriptions":
                if (kwargs.get("params") or {}).get("agent") == "cursor":
                    return {"subscriptions": [dict(_GLASS_IMPRESARIO_SUBSCRIPTION)]}
                return {"subscriptions": []}
            if (
                method == "GET"
                and path.startswith("/threads/")
                and path.endswith("/lane-current")
            ):
                tid = path.split("/")[2]
                if tid == worker_thread:
                    return {"parent_thread": "20995"}
                return {"parent_thread": None}
            raise AssertionError(f"unexpected bus call {method} {path}")

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert len(store.pending) == 1
    assert store.pending[0]["agent"] == "cursor"
    assert store.pending[0]["display"] == "8"


_UUID_MAILBOX_SENDER = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
_SUPERVISOR_GATE_RULES = frozenset({"wake_grok_allowlist", "wake_gate_or_directive"})


def test_uuid_sender_supervisor_head_parity_contract() -> None:
    """UUID mailbox senders must use allowlisted heads to wake a :8 cursor supervisor (friction:42964 Leg B)."""
    base = {
        "to_agent": "cursor",
        "from_agent": _UUID_MAILBOX_SENDER,
        "thread": "22514",
    }
    for head in (
        "PLAN",
        "BLOCKED",
        "RESULT",
        "REPORT",
        "READY",
        "VERDICT",
        "REVIEW",
        "ESCALATE",
        "DONE",
        "FAILED",
        "QUESTION",
        "CLOSEOUT",
    ):
        verdict = qualify(
            {**base, "subject": f"{head}|supervisor-gate-probe"}, cse=False, store=None
        )
        assert verdict.wake is True, head
        assert verdict.rule_id in _SUPERVISOR_GATE_RULES, (head, verdict.rule_id)
    # Intentional: RECEIPT/CHECKPOINT are ack-class; UUID senders do not wake :8 on them.
    for subject in ("RECEIPT|coord-ack", "CHECKPOINT|leg-boundary"):
        verdict = qualify({**base, "subject": subject}, cse=False, store=None)
        assert verdict.wake is False, subject
        assert verdict.rule_id == "default_suppress", subject
    api_base = {
        "to_agent": "cursor",
        "from_agent": "artisan",
        "thread": "22554",
    }
    api_done = qualify(
        {**api_base, "subject": "DONE|api-role-final"}, cse=False, store=None
    )
    assert api_done.wake is True
    assert api_done.rule_id == "wake_peer_sender"
    api_cp = qualify(
        {**api_base, "subject": "CHECKPOINT|api-role-intermediate"},
        cse=False,
        store=None,
    )
    assert api_cp.wake is False
    assert api_cp.rule_id == "default_suppress"
    receipt = qualify(
        {
            "to_agent": "cursor",
            "from_agent": "grok-bot-gc1",
            "subject": "RECEIPT|peer-ack",
            "thread": "22514",
        },
        cse=False,
        store=None,
    )
    assert receipt.wake is True
    assert receipt.rule_id == "wake_peer_sender"


def test_orion_answer_to_cursor_wakes_peer() -> None:
    verdict = qualify(
        {
            "to_agent": "cursor",
            "from_agent": "orion",
            "subject": "ANSWER|INTERLOCUTION|orion-paste-architecture",
            "thread": "21207",
        },
        cse=False,
        store=None,
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_peer_sender"


def test_admit_echo_dropped_for_peer_sender_regardless_of_from_agent() -> None:
    """Rec 4: coord admit pointer must not wake before worker CLOSEOUT."""
    house = "20995"
    imp = "composer-impresario-dbbfc4c2"
    base = {
        "to_agent": imp,
        "subject": "cursor-sdk generate admitted",
        "thread": house,
        "id": 1,
    }
    for from_agent in ("cursor", imp, "composer-impresario-other"):
        verdict = qualify({**base, "from_agent": from_agent}, cse=False, store=None)
        assert verdict.wake is False, from_agent
        if from_agent == imp:
            assert verdict.rule_id == "drop_self_or_broadcast"
        else:
            assert verdict.rule_id == "drop_dispatch_intermediate"


def test_house_land_wake_budget_admit_echo_zero_closeout_and_verdict_wake() -> None:
    """Full land lifecycle: admit echo silent; worker closeout + review wake; total <= 2."""
    house = "20995"
    worker = "21015"
    imp = "composer-impresario-dbbfc4c2"
    wake_count = 0
    lifecycle = (
        {
            "to_agent": imp,
            "from_agent": imp,
            "subject": "cursor-sdk generate admitted",
            "thread": house,
        },
        {
            "to_agent": imp,
            "from_agent": "cursor-sdk",
            "subject": "CLOSEOUT|cursor-sdk CLOSEOUT lane-21015 contract=implement",
            "thread": worker,
        },
        {
            "to_agent": imp,
            "from_agent": "web-anthropic",
            "subject": "VERDICT|PASS|wake-noise-reduction",
            "thread": house,
        },
    )
    for turn in lifecycle:
        verdict = qualify(turn, cse=False, store=None)
        if verdict.wake:
            wake_count += 1
        if turn["subject"] == "cursor-sdk generate admitted":
            assert verdict.wake is False
            assert verdict.rule_id in {
                "drop_dispatch_intermediate",
                "drop_self_or_broadcast",
            }
        if turn["thread"] == worker and "CLOSEOUT" in turn["subject"]:
            assert verdict.wake is True
    assert wake_count <= 2


def test_scan_once_ignores_threads_below_cursor(tmp_path, monkeypatch) -> None:
    """Break: scan_once writes a ledger row for turns at or below the high water."""
    from pathlib import Path

    ledger = Path(tmp_path) / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.expire_overdue_hops",
        lambda store: None,
    )

    class _Store:
        def cursor(self) -> dict:
            return {"high_water_turn_id": 50}

        def since(self, high: int) -> list:
            assert high == 50
            return []

    scan_once(_Store())  # type: ignore[arg-type]
    assert not ledger.exists()


def test_fire_cse_followup_dry_run_writes_nothing(tmp_path, monkeypatch) -> None:
    """Break: dry_run=True still appends a CSE wake row."""
    from pathlib import Path
    from unittest.mock import patch

    from bus_watch.cse_followup import fire_cse_followup

    ledger = Path(tmp_path) / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/chat/abc",
                "registration_id": "reg-1",
                "url": "https://claude.ai/chat/abc",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE", "10479", dry_run=True)
    assert out["dry_run"] is True
    assert not ledger.exists()


def test_fire_cse_followup_appends_wake_when_not_dry_run(tmp_path, monkeypatch) -> None:
    """Break: a live CSE followup returns ok and writes no kind=wake row."""
    import json
    from pathlib import Path
    from unittest.mock import MagicMock, patch

    from bus_watch.cse_followup import fire_cse_followup

    ledger = Path(tmp_path) / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )

    def _post(method: str, url: str, *, json=None, timeout: float):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b'{"ok": true, "send_verified": true}'
        resp.json.return_value = {"ok": True, "send_verified": True}
        return resp

    with (
        patch(
            "bus_watch.cse_followup.resolve_cse_identity",
            return_value={
                "chat_url": "https://claude.ai/chat/abc",
                "registration_id": "reg-1",
                "url": "https://claude.ai/chat/abc",
            },
        ),
        patch(
            "bus_watch.cse_followup.project_ask_base_url",
            return_value="http://127.0.0.1:9191",
        ),
    ):
        out = fire_cse_followup("WAKE", "10479", dry_run=False, post=_post)
    assert out["ok"] is True
    rows = [
        json.loads(line) for line in ledger.read_text().splitlines() if line.strip()
    ]
    wakes = [r for r in rows if r.get("kind") == "wake"]
    assert len(wakes) == 1
    assert wakes[0]["op"] == "cse_followup"


def test_deliver_cse_writes_no_ledger_row(tmp_path, monkeypatch) -> None:
    """Break: _deliver_cse appends a ledger row itself instead of only calling fire."""
    from pathlib import Path
    from unittest.mock import patch

    from bus_watch.endpoint_wake_dispatcher import _deliver_cse

    ledger = Path(tmp_path) / "cursor-bridge-tab-wake-ledger.jsonl"
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.ledger_path",
        lambda watch_dir=None: ledger,
    )
    calls: list[bool] = []

    def _fire(*args, **kwargs):
        calls.append(bool(kwargs.get("dry_run")))
        return {
            "ok": True,
            "registration_id": "reg-1",
            "send_verified": True,
            "source": "local",
        }

    with patch("bus_watch.cse_followup.fire_cse_followup", _fire):
        state, reason, reg = _deliver_cse(
            [{"thread_id": "10479", "turn_number": 3}],
            {"chat_url": "https://claude.ai/chat/abc", "registration_id": "reg-1"},
        )
    assert state
    assert calls == [True, False]
    assert reg == "reg-1"
    assert reason is not None
    assert not ledger.exists()


def test_agent_id_mismatch_records_refused_not_pending(db: None) -> None:
    detail = create_thread(thread_id=None, slug="aid-mismatch")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")["wake_id"]
    assert wake
    record_wake_result(
        wake,
        "refused",
        "agent_id_mismatch",
        "exact",
        1,
        None,
        now="2020-01-01 00:00:02",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE wake_id=?",
            (wake,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["reason"] == "agent_id_mismatch"
    assert claim_due_wakes(now="2020-01-01 00:00:30")["wake_id"] is None


def test_composer_defer_sweep_escalates_once_to_maestro(db: None) -> None:
    from bus_watch.endpoint_wake_composer_defer import (
        COMPOSER_DEFER_SWEEP,
        escalate_composer_deferred_on_sweep,
        finalize_composer_defer_pass,
    )

    detail = create_thread(thread_id=None, slug="composer-sweep")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")["wake_id"]
    assert wake
    record_wake_result(
        wake,
        "pending",
        "composer_not_empty",
        "exact",
        1,
        None,
        now="2020-01-01 00:00:02",
    )
    finalize_composer_defer_pass()
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule FROM endpoint_wake_ledger WHERE id IN "
            "(SELECT id FROM endpoint_wake_ledger WHERE thread_id=? LIMIT 1)",
            (thread,),
        ).fetchone()
    assert row["decided_by_rule"] == COMPOSER_DEFER_SWEEP

    class _Store:
        posts: list[dict] = []

        def create_turn(self, **kwargs: object) -> dict:
            _Store.posts.append(dict(kwargs))
            return {"ok": True}

    store = _Store()
    from agent_bus_store.db.connection import write_connect

    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET wake_attempt_count=12, created_at=datetime('now'), "
            "enqueued_at=datetime('now') WHERE thread_id=?",
            (thread,),
        )
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(_Store.posts) == 1
    assert _Store.posts[0]["to_agent"] == "maestro"
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    from agent_bus_store.db.endpoint_wake_attempts import COMPOSER_DEFER_DROP_NOTICED

    assert row["decided_by_rule"] == COMPOSER_DEFER_DROP_NOTICED
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(_Store.posts) == 1


def _seed_legacy_pending_wake_row(
    thread: str,
    *,
    reason: str,
    decided_by_rule: str | None = None,
) -> int:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject=f"LEGACY|{reason}",
        body="legacy",
    )
    from agent_bus_store.db.connection import write_connect

    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule) VALUES (?,?,?,?, 'pending', ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                reason,
                "2020-01-01 00:00:00",
                decided_by_rule,
            ),
        )
    return turn_id


def test_legacy_agent_id_mismatch_pending_refused_after_normalize(db: None) -> None:
    from bus_watch.endpoint_wake_composer_defer import (
        LEGACY_NORMALIZE_AGENT_MISMATCH,
        normalize_legacy_pending_wakes,
    )

    detail = create_thread(thread_id=None, slug="legacy-aid-mismatch")
    assert detail is not None
    thread = str(detail["id"])
    _seed_legacy_pending_wake_row(thread, reason="agent_id_mismatch")
    stats = normalize_legacy_pending_wakes()
    assert stats["agent_id_mismatch_refused"] == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, decided_by_rule FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["reason"] == "agent_id_mismatch"
    assert row["decided_by_rule"] == LEGACY_NORMALIZE_AGENT_MISMATCH


def test_legacy_composer_not_empty_two_pass_escalation(db: None) -> None:
    from bus_watch.endpoint_wake_composer_defer import (
        COMPOSER_DEFER_SWEEP,
        run_dispatcher_pass_legacy_sweep,
    )

    detail = create_thread(thread_id=None, slug="legacy-composer")
    assert detail is not None
    thread = str(detail["id"])
    _seed_legacy_pending_wake_row(thread, reason="composer_not_empty")

    class _Store:
        posts: list[dict] = []

        def create_turn(self, **kwargs: object) -> dict:
            _Store.posts.append(dict(kwargs))
            return {"ok": True}

    store = _Store()
    pass1 = run_dispatcher_pass_legacy_sweep(store)
    assert pass1["composer_defer_armed"] == 1
    assert pass1["escalated"] == 0
    assert pass1["finalized"] == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["decided_by_rule"] == COMPOSER_DEFER_SWEEP

    from agent_bus_store.db.connection import write_connect

    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET created_at=datetime('now'), enqueued_at=datetime('now') "
            "WHERE thread_id=?",
            (thread,),
        )
    pass2 = run_dispatcher_pass_legacy_sweep(store)
    assert pass2["composer_defer_armed"] == 0
    assert pass2["escalated"] == 0
    assert len(_Store.posts) == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule, wake_next_attempt_at FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["wake_next_attempt_at"]

    pass3 = run_dispatcher_pass_legacy_sweep(store)
    assert pass3["escalated"] == 0
    assert len(_Store.posts) == 0


def test_normalize_legacy_pending_wakes_idempotent(db: None) -> None:
    from bus_watch.endpoint_wake_composer_defer import (
        COMPOSER_DEFER_ARMED,
        normalize_legacy_pending_wakes,
    )

    detail = create_thread(thread_id=None, slug="legacy-idempotent")
    assert detail is not None
    thread = str(detail["id"])
    _seed_legacy_pending_wake_row(thread, reason="agent_id_mismatch")
    _seed_legacy_pending_wake_row(thread, reason="composer_not_empty")
    first = normalize_legacy_pending_wakes()
    assert first["agent_id_mismatch_refused"] == 1
    assert first["composer_defer_armed"] == 1
    second = normalize_legacy_pending_wakes()
    assert second["agent_id_mismatch_refused"] == 0
    assert second["composer_defer_armed"] == 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT reason, decided_by_rule FROM endpoint_wake_ledger "
            "WHERE thread_id=? ORDER BY reason",
            (thread,),
        ).fetchall()
    assert rows[0]["reason"] == "agent_id_mismatch"
    assert rows[0]["decided_by_rule"] == "legacy_normalize:agent_id_mismatch"
    assert rows[1]["reason"] == "composer_not_empty"
    assert rows[1]["decided_by_rule"] == COMPOSER_DEFER_ARMED


def test_falsifier_no_pending_agent_id_mismatch_or_composer_not_empty_after_sweep(
    db: None,
) -> None:
    from bus_watch.endpoint_wake_composer_defer import run_dispatcher_pass_legacy_sweep

    detail = create_thread(thread_id=None, slug="falsifier-sweep")
    assert detail is not None
    thread = str(detail["id"])
    _seed_legacy_pending_wake_row(thread, reason="agent_id_mismatch")
    _seed_legacy_pending_wake_row(thread, reason="composer_not_empty")

    class _Store:
        def create_turn(self, **kwargs: object) -> dict:
            return {"ok": True}

    store = _Store()
    run_dispatcher_pass_legacy_sweep(store)
    run_dispatcher_pass_legacy_sweep(store)
    with connect() as conn:
        bad = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger "
            "WHERE state='pending' AND reason IN ('agent_id_mismatch','composer_not_empty')"
        ).fetchone()
    assert int(bad["n"]) == 0


def test_composer_defer_decision_does_not_use_wall_clock() -> None:
    from bus_watch.endpoint_wake_composer_defer import defer_path_avoids_wall_clock

    assert defer_path_avoids_wall_clock() is True


# --- refuse_tab_unaddressed scope/surface/remap gating (43c9e95a fix-forward) ---


def _seed_row(
    *,
    agent: str,
    scope: str,
    agent_id: str,
    surface: str = "cursor",
) -> None:
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    subscribe_endpoint(
        agent=agent,
        thread_scope=scope,
        surface=surface,
        display="8",
        title="t",
        agent_id=agent_id,
        bound_by="test",
    )


class _TabScanStore:
    """scan_once store that resolves exact rows first, then a ``*`` row as wildcard."""

    def __init__(
        self, *, holder: dict | None = None, parent_scope: str | None = None
    ) -> None:
        self.pending: list[dict] = []
        self._holder = holder
        self._parent_scope = parent_scope

    def cursor(self) -> dict:
        return get_cursor()

    def since(self, after_id: int, limit: int = 50) -> list[dict]:
        with connect() as c:
            rows = c.execute(
                "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
        self.pending = rows
        return advance_scan(
            "endpoint-wake-dispatcher",
            expected,
            new_hw,
            rows,
            now="2020-01-01 20:00:00",
        )

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        from agent_bus_store.db.endpoint_subscriptions import current_subscription

        row = current_subscription(agent, thread_id)
        if row and row.get("state") == "subscribed":
            return ("exact", row)
        if self._parent_scope:
            row = current_subscription(agent, self._parent_scope)
            if row and row.get("state") == "subscribed":
                return ("parent", row)
        if agent != "cursor":
            # Transport falls back to the thread's cursor row for a suffix-bearing
            # address (house-lane branch); the suffix match then decides.
            row = current_subscription("cursor", thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
        return None

    def holder(self, lane: str) -> dict | None:
        return self._holder

    def lane_parent(self, lane: str) -> str | None:
        if self._parent_scope and lane != self._parent_scope:
            return self._parent_scope
        return None

    def subscriptions(self, agent: str = "cursor") -> list[dict]:
        from agent_bus_store.db.endpoint_subscriptions import list_subscriptions

        return [
            r for r in list_subscriptions(agent=agent) if r.get("state") == "subscribed"
        ]

    def turns(self, thread: str, **params: object) -> dict:
        after = int(params.get("after_turn", 0) or 0)  # type: ignore[arg-type]
        limit = int(params.get("last", 50))  # type: ignore[arg-type]
        with connect() as c:
            rows = c.execute(
                "SELECT id, thread, turn_number, from_agent, to_agent, subject, body "
                "FROM turns WHERE thread=? AND turn_number>? "
                "ORDER BY turn_number ASC LIMIT ?",
                (thread, after, limit + 1),
            ).fetchall()
        truncated = len(rows) > limit
        page = rows[:limit] if truncated else rows
        next_after = int(page[-1]["turn_number"]) if truncated and page else None
        return {
            "turns": [dict(r) for r in page],
            "truncated": truncated,
            "next_after_turn": next_after,
        }


def _rewind_cursor() -> None:
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)


class _TabFireStore(_TabScanStore):
    """``scan_once`` + ``claim_due_wakes`` + ``record_wake_result`` for round-3 fire proofs."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.results: list[dict] = []

    def claim(self) -> dict:
        return claim_due_wakes(now="2020-01-01 20:00:10")

    def result(self, **payload: object) -> int:
        self.results.append(dict(payload))
        return record_wake_result(
            str(payload["wake_id"]),
            str(payload["outcome"]),
            str(payload["reason"]) if payload["reason"] is not None else None,
            str(payload["route"]) if payload["route"] is not None else None,
            payload["subscription_id"],  # type: ignore[arg-type]
            payload["cse_registration_id"],  # type: ignore[arg-type]
        )

    def unsubscribe(self, *args: object, **kwargs: object) -> dict:
        return {}


def _new_thread(slug: str) -> str:
    detail = create_thread(thread_id=None, slug=slug)
    assert detail is not None
    return str(detail["id"])


_TAB_A = "cd0f990b-9ce2-4b58-bf9b-2cb7ac3491a6"
_TAB_B = "db36e9f8-e76f-49e7-bd57-c9c4ffa88195"
_TAB_B = "7e0b90ac-d861-4ae2-a484-17d57c193eeb"


@pytest.mark.parametrize(
    "subject",
    [
        "DONE| 43c9e95a",
        "VERDICT| gate",
        "PLAN| row 19",
        "QUESTION| scope",
        "RULING| bind",
        "AMEND| packet",
        "SEAT_REGISTRATION| maestro",
    ],
)
def test_t1_maestro_direct_address_not_refused_by_tab_row(
    db: None, subject: str
) -> None:
    """T1: a tab-bound cursor row must not swallow ``to=maestro`` on the lane."""
    thread = _new_thread("21724-t1-maestro")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent="maestro", scope=thread, agent_id="maestro-tab")
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="maestro",
        subject=subject,
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore(holder={"agent": "maestro"})
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]
    assert store.pending[0]["decided_by_rule"] == "wake_maestro_direct_address"


def test_t1_plain_ack_to_maestro_still_drops_as_ack_route_coord(db: None) -> None:
    thread = _new_thread("21724-t1-ack")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent="maestro", scope=thread, agent_id="maestro-tab")
    insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="maestro",
        subject="ACK|plain",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore(holder={"agent": "maestro"})
    scan_once(store)  # type: ignore[arg-type]
    assert [(r["state"], r["reason"]) for r in store.pending] == [
        ("skipped", "suppressed:drop_ack_route_coord")
    ]


def test_t2_peer_sender_wakes_on_thread_with_tab_bound_cursor_row(db: None) -> None:
    """T2: peer-sender turns to non-cursor addresses are untouched by the refusal."""
    thread = _new_thread("21724-t2-peer")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent="maestro", scope=thread, agent_id="maestro-tab")
    _seed_row(agent="grok-bot-8e611904", scope=thread, agent_id="grok-tab")
    a_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="REPORT|15851 leg",
        body="b",
    )
    b_id, _ts2, _n2, _c2 = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="grok-bot-8e611904",
        subject="REPORT|15851 leg",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [a_id, b_id]
    assert {r["decided_by_rule"] for r in store.pending} == {"wake_peer_sender"}


def _scan_waiter_stamp_delivery(
    *,
    admit_subject: str,
    thread_label: str,
) -> None:
    """Shared 43240-style stamp + frontier-admit reply delivery assertion."""
    from bus_watch.endpoint_wake_dispatcher import _reset_waiter_stamp_cache

    _reset_waiter_stamp_cache()
    thread = _new_thread(thread_label)
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject=admit_subject,
        body=f"waiter: {_TAB_A}\nWorker admitted",
    )
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — lane 22510",
        body="done",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    wake = next(r for r in store.pending if r.get("turn_id") == t_id)
    assert wake["agent"] == "cursor"
    assert wake["delivery_agent_id"] == _TAB_A


def test_waiter_stamp_api_role_new_thread_subject(db: None) -> None:
    _scan_waiter_stamp_delivery(
        admit_subject="synthesizer generate — req-new-thread",
        thread_label="22662-waiter-api-new",
    )


def test_waiter_stamp_api_role_reuse_thread_subject(db: None) -> None:
    _scan_waiter_stamp_delivery(
        admit_subject="composer generate — req-reuse-thread",
        thread_label="22662-waiter-api-reuse",
    )


def test_waiter_stamp_cdp_generate_subject(db: None) -> None:
    _scan_waiter_stamp_delivery(
        admit_subject="cdp generate — exec-cdp-001",
        thread_label="22662-waiter-cdp",
    )


def test_scan_from_cursor_wakes_waiter_not_tombstoned(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        RuleVerdict,
        _reset_waiter_stamp_cache,
        qualify,
    )

    _reset_waiter_stamp_cache()

    def _qualify(turn: dict, **kwargs: object) -> RuleVerdict:
        if str(turn.get("subject") or "") == "RESULT| cursor-peer-probe":
            return RuleVerdict(True, "wake_peer_sender")
        return qualify(turn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.qualify",
        _qualify,
    )
    thread = _new_thread("22662-cursor-peer-wake")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nWorker admitted",
    )
    peer_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="frontier-admit",
        subject="RESULT| cursor-peer-probe",
        body="done",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    wake = [r for r in store.pending if r.get("turn_id") == peer_id]
    assert len(wake) == 1
    assert wake[0].get("state") != "skipped"
    assert wake[0]["delivery_agent_id"] == _TAB_A


def test_waiter_stamp_frontier_admit_synthesizer_reply_wakes_tab(db: None) -> None:
    """Friction 43240: stamped waiter + gate address override delivers to cursor tab."""
    parent = _new_thread("22662-waiter-parent")
    child = _new_thread("22662-waiter-child")
    from agent_bus_store.db.lane_associations import associate_lane

    associate_lane(
        thread_id=child,
        parent_thread_id=parent,
        lane_role="sub_mission",
        bound_by="test",
    )
    _seed_row(agent="cursor", scope=parent, agent_id=_TAB_A)
    insert_turn(
        thread=parent,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nWorker `{child}`",
    )
    t_id, _ts, _n, _c = insert_turn(
        thread=child,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — lane 22510",
        body="done",
    )
    _rewind_cursor()
    store = _TabScanStore(parent_scope=parent)
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending if r.get("state") != "skipped"] == [
        t_id
    ]
    wake = next(r for r in store.pending if r.get("turn_id") == t_id)
    assert wake["agent"] == "cursor"
    assert wake["delivery_agent_id"] == _TAB_A


class _FrontierAdmitScanStore(_TabScanStore):
    """No synthetic cursor fallback for ``frontier-admit`` (43240 negative)."""

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        if agent == "frontier-admit":
            return None
        return super().subscription(agent, thread_id, raw_to=raw_to)


def test_waiter_stamp_absent_frontier_admit_reply_stays_silent(db: None) -> None:
    """Rollout window: without ``waiter:`` stamp, frontier-admit synthesizer is silent."""
    thread = _new_thread("22662-no-waiter")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — lane 22510",
        body="done",
    )
    _rewind_cursor()
    store = _FrontierAdmitScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r for r in store.pending if r.get("state") != "skipped"] == []


def test_scan_drop_waiter_own_seat_tombstone(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch.endpoint_wake_dispatcher import RuleVerdict, qualify

    def _qualify(turn: dict, **kwargs: object) -> RuleVerdict:
        if str(turn.get("subject") or "") == "RESULT| waiter-own-seat-probe":
            return RuleVerdict(True, "wake_peer_sender")
        return qualify(turn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.qualify",
        _qualify,
    )
    from uuid import uuid4

    impresario = f"composer-impresario-{uuid4().hex[:8]}"
    thread = _new_thread("22662-scan-own-seat")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent=impresario, scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nWorker admitted",
    )
    peer_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent=impresario,
        to_agent="frontier-admit",
        subject="RESULT| waiter-own-seat-probe",
        body="done",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    skipped = [
        r
        for r in store.pending
        if r.get("turn_id") == peer_id and r.get("state") == "skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0]["decided_by_rule"] == "drop_waiter_own_seat"


def test_is_waiter_own_seat_wake_matches_tab_holders(db: None) -> None:
    """R2: uuid tab holders and impresario seats suppress; shared ``from=cursor`` does not."""
    from uuid import uuid4

    from bus_watch.endpoint_wake_dispatcher import _is_waiter_own_seat_wake

    impresario = f"composer-impresario-{uuid4().hex[:8]}"
    thread = _new_thread("22662-own-seat-unit")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent=impresario, scope=thread, agent_id=_TAB_A)
    store = _TabScanStore()
    base_turn = {
        "thread": thread,
        "to_agent": "frontier-admit",
        "subject": "RESULT| peer",
    }
    cursor_resolved = (
        "exact",
        {"agent_id": _TAB_A, "agent": "cursor", "thread_scope": thread},
    )
    turn_cursor = {**base_turn, "from_agent": "cursor"}
    assert not _is_waiter_own_seat_wake(store, turn_cursor, _TAB_A, cursor_resolved)  # type: ignore[arg-type]
    turn_imp = {**base_turn, "from_agent": impresario}
    imp_resolved = (
        "exact",
        {"agent_id": _TAB_A, "agent": impresario, "thread_scope": thread},
    )
    assert _is_waiter_own_seat_wake(store, turn_imp, _TAB_A, imp_resolved)  # type: ignore[arg-type]
    turn_outsider = {**base_turn, "from_agent": "synthesizer"}
    assert not _is_waiter_own_seat_wake(store, turn_outsider, _TAB_A, cursor_resolved)  # type: ignore[arg-type]


def test_waiter_two_admit_stamps_latest_wins(db: None) -> None:
    thread = _new_thread("22662-two-stamp")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_B)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nfirst",
    )
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_B}\nsecond",
    )
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — x",
        body="done",
    )
    _rewind_cursor()
    store = _FrontierAdmitScanStore()
    scan_once(store)  # type: ignore[arg-type]
    wake = next(r for r in store.pending if r.get("turn_id") == t_id)
    assert wake["delivery_agent_id"] == _TAB_B


def test_waiter_quoted_in_later_turn_ignored(db: None) -> None:
    thread = _new_thread("22662-spoof")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nadmit",
    )
    insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="CLOSEOUT| quote",
        body=f"see waiter: {_TAB_B} in prose",
    )
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — y",
        body="done",
    )
    _rewind_cursor()
    store = _FrontierAdmitScanStore()
    scan_once(store)  # type: ignore[arg-type]
    wake = next(r for r in store.pending if r.get("turn_id") == t_id)
    assert wake["delivery_agent_id"] == _TAB_A


def test_waiter_admit_stamp_beyond_first_page(db: None) -> None:
    from bus_watch.endpoint_wake_dispatcher import _parse_waiter_from_admit_turns

    thread = _new_thread("22662-deep-stamp")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="cursor-sdk generate admitted",
        body=f"waiter: {_TAB_A}\nold",
    )
    for i in range(105):
        insert_turn(
            thread=thread,
            from_agent="maestro",
            to_agent="cursor",
            subject=f"FACT| filler {i}",
            body="x",
        )
    _rewind_cursor()
    store = _TabScanStore()
    assert _parse_waiter_from_admit_turns(store, thread) == _TAB_A  # type: ignore[arg-type]


class _CountingNoStampStore(_FrontierAdmitScanStore):
    def __init__(self) -> None:
        super().__init__()
        self.route_calls = 0
        self.turns_calls = 0

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        self.route_calls += 1
        return super().subscription(agent, thread_id, raw_to=raw_to)

    def turns(self, thread: str, **params: object) -> dict:
        self.turns_calls += 1
        return super().turns(thread, **params)


def test_no_stamp_scan_skips_waiter_turns_lookup(db: None) -> None:
    from bus_watch.endpoint_wake_dispatcher import _reset_waiter_stamp_cache

    _reset_waiter_stamp_cache()
    thread = _new_thread("22662-nostamp-io")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="synthesizer",
        subject="REPORT| leg",
        body="b",
    )
    _rewind_cursor()
    store = _CountingNoStampStore()
    scan_once(store)  # type: ignore[arg-type]
    assert store.turns_calls == 0
    assert store.route_calls >= 1


def test_unstamped_cursor_at_most_one_cold_lookup_across_scans(db: None) -> None:
    from bus_watch.endpoint_wake_dispatcher import _reset_waiter_stamp_cache

    _reset_waiter_stamp_cache()
    thread = _new_thread("22662-cursor-cold-once")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="REPORT| leg",
        body="b",
    )
    _rewind_cursor()
    store = _CountingNoStampStore()
    scan_once(store)  # type: ignore[arg-type]
    assert store.turns_calls <= 1
    first = store.turns_calls
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="REPORT| leg-2",
        body="b2",
    )
    _rewind_cursor()
    scan_once(store)  # type: ignore[arg-type]
    assert store.turns_calls == first


def test_waiter_stamp_retries_after_transient_turns_failure(db: None) -> None:
    """B1: a failed cold lookup is not cached; the next scan finds the stamp."""
    from bus_watch.endpoint_wake_dispatcher import (
        _WAITER_THREAD_CACHE,
        _reset_waiter_stamp_cache,
    )

    thread = _new_thread("22662-turns-flaky")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="dispatch",
        to_agent="dispatch",
        subject="synthesizer generate — flaky-req",
        body=f"waiter: {_TAB_A}\nWorker admitted",
    )
    _rewind_cursor()
    scan_once(_TabScanStore())  # type: ignore[arg-type]
    _reset_waiter_stamp_cache()

    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="synthesizer",
        to_agent="frontier-admit",
        subject="synthesizer reply — flaky",
        body="done",
    )

    class _FlakyTurnsStore(_TabScanStore):
        def __init__(self) -> None:
            super().__init__()
            self.turns_failures = 0

        def turns(self, thread: str, **params: object) -> dict:
            self.turns_failures += 1
            if self.turns_failures == 1:
                raise OSError("agent-bus warming up")
            return super().turns(thread, **params)

    store = _FlakyTurnsStore()
    scan_once(store)  # type: ignore[arg-type]
    assert store.turns_failures == 1
    assert thread not in _WAITER_THREAD_CACHE

    _rewind_cursor()
    scan_once(store)  # type: ignore[arg-type]
    wake = next(r for r in store.pending if r.get("turn_id") == t_id)
    assert wake["delivery_agent_id"] == _TAB_A


def test_web_anthropic_turn_skips_waiter_turns_lookup(db: None) -> None:
    thread = _new_thread("22662-wa-io")
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="web-anthropic",
        subject="ANSWER| batch",
        body="b",
    )
    _rewind_cursor()
    store = _CountingNoStampStore()
    scan_once(store)  # type: ignore[arg-type]
    assert store.turns_calls == 0


def test_t3_gate_wake_remap_address_enqueues_on_tab_bound_row(db: None) -> None:
    """T3/H2: ``to=git-integration-worker`` CLOSEOUT remaps to cursor and must wake."""
    thread = _new_thread("21724-t3-remap")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="git-integration-worker",
        subject="CLOSEOUT| lane land",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]
    assert store.pending[0]["delivery_agent_id"] == _TAB_A


def test_t3_bare_cursor_on_parent_row_keeps_legacy_pickup(db: None) -> None:
    """T3/H3: only an exact-scope row owns the thread; an ancestor row must not refuse."""
    parent = _new_thread("21724-t3-parent")
    thread = _new_thread("21724-t3-child")
    _seed_row(agent="cursor", scope=parent, agent_id=_TAB_A)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore(parent_scope=parent)
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]


def test_bare_cursor_on_exact_tab_bound_row_enqueues(db: None) -> None:
    """Round-3: bare ``to=cursor`` on an exact tab-bound row enqueues for fire-time remap."""
    thread = _new_thread("21724-bare-enqueue")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]


def _suffix_row(*, scope: str, agent_id: str, row_id: int) -> dict:
    return {
        "id": row_id,
        "agent": "cursor",
        "thread_scope": scope,
        "state": "subscribed",
        "surface": "cursor",
        "display": "8",
        "title": f"{scope} house",
        "agent_id": agent_id,
    }


def _suffix_lookup_store(rows: list[dict]) -> StoreClient:
    """Real transport labelling and suffix lookup over a canned payload."""

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs):
            if path == "/endpoint-subscriptions":
                agent = (kwargs.get("params") or {}).get("agent")
                return {"subscriptions": [r for r in rows if r.get("agent") == agent]}
            raise AssertionError(f"unexpected bus call {method} {path}")

    return StubStore()


def test_suffix_address_redirects_to_the_row_that_suffix_names(db: None) -> None:
    """H1: a CLOSEOUT to the departed tab delivers to the row that tab still holds."""
    from bus_watch.endpoint_wake_dispatcher import _cursor_tab_route

    resolved_b = ("exact", _suffix_row(scope="21724", agent_id=_TAB_B, row_id=20))
    row_a = _suffix_row(scope="18460", agent_id=_TAB_A, row_id=19)
    store = _suffix_lookup_store([resolved_b[1], row_a])
    reason, route = _cursor_tab_route(
        store, f"composer-impresario-{_TAB_A[:8]}", resolved_b
    )
    assert reason is None
    assert route is not None
    assert route[0] == "agent_id"
    assert route[1]["agent_id"] == _TAB_A


def test_suffix_address_with_no_such_row_is_remapped_on_an_exact_row(db: None) -> None:
    """H1: with the addressed tab gone from every scope, an exact row yields stale_name."""
    from bus_watch.endpoint_wake_dispatcher import _cursor_tab_route

    resolved_b = ("exact", _suffix_row(scope="21724", agent_id=_TAB_B, row_id=20))
    store = _suffix_lookup_store([resolved_b[1]])
    reason, route = _cursor_tab_route(
        store, f"composer-impresario-{_TAB_A[:8]}", resolved_b
    )
    assert reason == "stale_name"
    assert route == resolved_b


def test_suffix_address_with_no_such_row_delivers_on_a_non_exact_row(db: None) -> None:
    """H1: an ancestor row does not own the thread, so it delivers rather than refuse."""
    from bus_watch.endpoint_wake_dispatcher import _cursor_tab_route

    resolved_b = ("parent", _suffix_row(scope="12286", agent_id=_TAB_B, row_id=20))
    store = _suffix_lookup_store([resolved_b[1]])
    reason, route = _cursor_tab_route(
        store, f"composer-impresario-{_TAB_A[:8]}", resolved_b
    )
    assert reason is None
    assert route == resolved_b


def test_suffix_address_for_right_tab_wakes(db: None) -> None:
    thread = _new_thread("21724-suffix-match")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_B)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent=f"composer-impresario-{_TAB_B[:8]}",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]


def test_cse_row_agent_id_is_a_lane_not_a_tab(db: None) -> None:
    """M2: a CSE row stores a lane id in ``agent_id``; it must not refuse a cursor turn."""
    from bus_watch.endpoint_wake_dispatcher import _cursor_tab_route

    cse = ("exact", {"surface": "cse", "agent_id": "21207"})
    assert _cursor_tab_route(_suffix_lookup_store([]), "cursor", cse) == (None, cse)


def test_orion_is_a_peer_sender_only_on_answer(db: None) -> None:
    """M1: orion peer status is subject-gated to ANSWER."""
    base = {"to_agent": "maestro", "from_agent": "orion", "id": 1}
    assert qualify({**base, "subject": "ANSWER|probe"}, cse=False).wake is True
    assert (
        qualify({**base, "subject": "ANSWER|probe"}, cse=False).rule_id
        == "wake_peer_sender"
    )
    assert qualify({**base, "subject": "CHATTER|probe"}, cse=False).wake is False


def test_deep_lane_house_row_is_labelled_parent_not_exact(
    db: None, monkeypatch
) -> None:
    """M1: a holder walk past _MAX_PARENT_HOPS must not hand back an "exact" row."""
    import types

    from bus_watch import endpoint_wake_dispatcher as dispatcher

    root = "12286"
    chain = ["21726", "21724", "21207", "15851", root]
    house_row = _suffix_row(scope=root, agent_id=_TAB_A, row_id=41)

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs):
            if path == "/endpoint-subscriptions":
                agent = (kwargs.get("params") or {}).get("agent")
                return {"subscriptions": [house_row] if agent == "cursor" else []}
            if path.endswith("/lane-current"):
                cur = path.split("/")[2]
                idx = chain.index(cur) if cur in chain else None
                parent = (
                    chain[idx + 1] if idx is not None and idx + 1 < len(chain) else None
                )
                return {"parent_thread": parent}
            raise AssertionError(f"unexpected bus call {method} {path}")

    store = StubStore()
    # Real transport: the deep thread is 4 hops under the house row, past
    # _MAX_PARENT_HOPS=3, so scope resolution misses and the house lane answers.
    assert store.subscription("cursor", chain[0]) is None
    monkeypatch.setattr(
        dispatcher,
        "resolve_turn_address",
        lambda thread, store: types.SimpleNamespace(lane_thread=root),
    )
    resolved = dispatcher._resolve_delivery_route(
        store, "cursor", chain[0], raw_to="cursor"
    )
    assert resolved is not None
    assert resolved[0] == "parent"
    reason, route = dispatcher._cursor_tab_route(store, "cursor", resolved)
    assert reason is None
    assert route == resolved


def test_orion_answer_gate_compares_only_the_first_token(db: None) -> None:
    """L3: ``ANSWER INTERLOCUTION|…`` is still an ANSWER head."""
    base = {"to_agent": "maestro", "from_agent": "orion", "id": 1}
    verdict = qualify({**base, "subject": "ANSWER INTERLOCUTION|probe"}, cse=False)
    assert verdict.wake is True
    assert verdict.rule_id == "wake_peer_sender"
    assert qualify({**base, "subject": "ANSWERING|probe"}, cse=False).wake is False


def test_r3_orion_answer_bare_cursor_on_exact_row_enqueues(db: None) -> None:
    thread = _new_thread("21724-r3-orion-answer")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="orion",
        to_agent="cursor",
        subject="ANSWER| probe",
        body="b",
    )
    _rewind_cursor()
    store = _TabScanStore()
    scan_once(store)  # type: ignore[arg-type]
    assert [r["turn_id"] for r in store.pending] == [t_id]
    assert store.pending[0]["decided_by_rule"] == "wake_peer_sender"


def test_r3_stale_suffix_on_exact_house_row_delivers_to_bound_tab_remapped(
    db: None,
) -> None:
    from unittest.mock import patch

    thread = _new_thread("21724-r3-stale-suffix")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_B)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent=f"composer-impresario-{_TAB_A[:8]}",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabFireStore()
    captured: list[dict] = []

    def _capture(_row: dict, sub: dict, _turn_arg: object, **kwargs: object) -> tuple:
        captured.append(sub)
        return ("delivered", "delivered")

    scan_once(store)  # type: ignore[arg-type]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert captured[0]["agent_id"] == _TAB_B
    assert store.results[-1]["route"] == "remapped"
    assert store.results[-1]["reason"] == "stale_name"
    with connect() as conn:
        row = conn.execute(
            "SELECT state, route, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "delivered"
    assert row["route"] == "remapped"
    assert row["reason"] == "stale_name"


def test_r3_bare_cursor_on_exact_row_delivers_bare_name(db: None) -> None:
    from unittest.mock import patch

    thread = _new_thread("21724-r3-bare-fire")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    t_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabFireStore()
    captured: list[dict] = []

    def _capture(_row: dict, sub: dict, _turn_arg: object, **kwargs: object) -> tuple:
        captured.append(sub)
        return ("delivered", "delivered")

    scan_once(store)  # type: ignore[arg-type]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert captured[0]["agent_id"] == _TAB_A
    assert store.results[-1]["route"] == "remapped"
    assert store.results[-1]["reason"] == "bare_name"
    with connect() as conn:
        row = conn.execute(
            "SELECT state, route, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "delivered"
    assert row["route"] == "remapped"
    assert row["reason"] == "bare_name"


def test_r3_remapped_keeps_delivery_reason_when_not_delivered(db: None) -> None:
    from unittest.mock import patch

    thread = _new_thread("21724-r3-composer-busy")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="cursor",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    store = _TabFireStore()
    scan_once(store)  # type: ignore[arg-type]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=("pending", "composer_busy"),
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert store.results[-1]["route"] == "remapped"
    assert store.results[-1]["reason"] == "composer_busy"


def test_r3_gate_remap_address_on_exact_row_is_not_labelled_remapped(db: None) -> None:
    from unittest.mock import patch

    thread = _new_thread("21724-r3-gate-remap-fire")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_A)
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="git-integration-worker",
        subject="CLOSEOUT| lane land",
        body="b",
    )
    _rewind_cursor()
    store = _TabFireStore()
    scan_once(store)  # type: ignore[arg-type]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        return_value=("delivered", "delivered"),
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert store.results[-1]["route"] == "exact"
    assert store.results[-1]["reason"] == "delivered"


def test_r3_fire_honours_live_suffix_row_redirect(db: None) -> None:
    from unittest.mock import patch

    thread = _new_thread("21724-r3-suffix-redirect")
    _seed_row(agent="cursor", scope=thread, agent_id=_TAB_B)
    insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent=f"composer-impresario-{_TAB_A[:8]}",
        subject="RULING| bind",
        body="b",
    )
    _rewind_cursor()
    row_a = _suffix_row(scope="18460", agent_id=_TAB_A, row_id=19)
    from bus_watch.endpoint_wake_transport import agent_id_matches_composer_suffix

    class _SuffixFireStore(_TabFireStore):
        def _subscription_by_cursor_agent_id_suffix(self, suffix: str):
            if agent_id_matches_composer_suffix(_TAB_A, suffix):
                return ("agent_id", row_a)
            return None

    store = _SuffixFireStore()
    captured: list[dict] = []

    def _capture(_row: dict, sub: dict, _turn_arg: object, **kwargs: object) -> tuple:
        captured.append(sub)
        return ("delivered", "delivered")

    scan_once(store)  # type: ignore[arg-type]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_capture,
    ):
        fire_due_wakes(store)  # type: ignore[arg-type]
    assert captured[0]["agent_id"] == _TAB_A
    assert store.results[-1]["route"] == "agent_id"
    assert store.results[-1]["reason"] == "delivered"


def test_run_dispatcher_emits_pass_failed_when_stage_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher

    emitted: list[dict[str, str]] = []

    def _normalize_boom() -> None:
        raise RuntimeError("normalize boom")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.normalize_legacy_pending_wakes",
        _normalize_boom,
    )
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_failed",
        lambda **kw: emitted.append(dict(kw)),
    )

    async def _sleep(_delay: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_dispatcher())
    assert len(emitted) == 1
    assert emitted[0]["error_class"] == "RuntimeError"
    assert "normalize boom" in emitted[0]["error_message"]
    assert emitted[0]["stage"] == "normalize_legacy_pending_wakes"


def test_run_dispatcher_survives_pass_failed_emit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import run_dispatcher

    def _normalize_boom() -> None:
        raise ValueError("stage fail")

    def _emit_boom(**_kw: object) -> None:
        raise OSError("telemetry down")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.normalize_legacy_pending_wakes",
        _normalize_boom,
    )
    monkeypatch.setattr(
        "bus_watch.events.emit_endpoint_wake_pass_failed",
        _emit_boom,
    )
    sleeps = {"n": 0}

    async def _sleep(_delay: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_dispatcher())
    assert sleeps["n"] == 1


@pytest.mark.parametrize(
    ("env_val", "expected"),
    [
        ("0", 900.0),
        ("-5", 900.0),
        ("nan", 900.0),
        ("inf", 900.0),
        ("abc", 900.0),
        pytest.param(None, 900.0, id="unset"),
    ],
)
def test_endpoint_wake_catchup_max_age_s_env_fallback(
    monkeypatch: pytest.MonkeyPatch, env_val: str | None, expected: float
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        _endpoint_wake_catchup_max_age_s,
        _turn_is_stale_catchup,
    )

    if env_val is None:
        monkeypatch.delenv("ENDPOINT_WAKE_CATCHUP_MAX_AGE_S", raising=False)
    else:
        monkeypatch.setenv("ENDPOINT_WAKE_CATCHUP_MAX_AGE_S", env_val)
    assert _endpoint_wake_catchup_max_age_s() == expected
    # Guard must not treat every turn as stale when env is misconfigured.
    from datetime import UTC, datetime

    now_epoch = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    recent = {"created_at": "2033-06-01T11:59:00Z"}
    assert _turn_is_stale_catchup(recent, now_epoch=now_epoch) is False


def test_turn_created_at_epoch_naive_iso_t_is_utc() -> None:
    from datetime import UTC, datetime

    from bus_watch.endpoint_wake_dispatcher import _turn_created_at_epoch

    ts = _turn_created_at_epoch("2033-06-01T12:00:00")
    assert ts == datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()


def test_scan_once_drop_stale_catchup_enqueues_fresh_only(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from agent_bus_store.db.connection import write_connect

    monkeypatch.setenv("ENDPOINT_WAKE_CATCHUP_MAX_AGE_S", "900")
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now,
    )
    detail = create_thread(thread_id=None, slug="stale-catchup")
    assert detail is not None
    thread = str(detail["id"])
    stale_id, _ts1, stale_n, _c1 = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|stale",
        body="old",
    )
    fresh_id, _ts2, fresh_n, _c2 = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|fresh",
        body="new",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01 00:00:00", stale_id),
        )
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2033-06-01T11:59:00Z", fresh_id),
        )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    pending: list[dict] = []
    scan_calls: list[tuple[int, int, list]] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at, created_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            pending.extend(rows)
            scan_calls.append((expected, new_hw, list(rows)))
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2033-06-01 12:00:01",
            )

        def holder(self, lane: str) -> dict:
            return {"registration_id": "reg-live", "seat_state": "driving"}

        def subscription(self, agent: str, thread_id: str):
            return (
                "cse",
                {
                    "surface": "cse",
                    "agent_id": thread_id,
                    "id": 1,
                    "state": "subscribed",
                },
            )

    scan_once(Store())  # type: ignore[arg-type]
    assert [
        p["turn_id"] for p in pending if p.get("state", "pending") == "pending"
    ] == [fresh_id]
    assert scan_calls[-1][1] == fresh_id
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) == fresh_id
    with connect() as conn:
        stale_row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (stale_id,),
        ).fetchone()
        fresh_row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?", (fresh_id,)
        ).fetchone()
        stale_drop = conn.execute(
            "SELECT reason FROM endpoint_wake_scan_drops WHERE turn_id=?",
            (stale_id,),
        ).fetchone()
    assert stale_row is None
    assert stale_drop is not None
    assert stale_drop["reason"] == "skipped:stale_catchup"
    assert fresh_row is not None
    assert fresh_row["state"] == "pending"
    assert stale_n and fresh_n


def test_resolve_cse_wake_target_registration_not_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01TestHouseless"
    sub = {
        "surface": "cse",
        "agent_id": reg,
        "thread_scope": "22760",
        "state": "subscribed",
    }
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=True, reason="ok", chat_url=chat),
    )

    class Store:
        def holder(self, lane_thread_id: str) -> dict:
            raise AssertionError(
                f"lane holder must not run for reg id: {lane_thread_id}"
            )

        def holder_by_registration(self, registration_id: str) -> dict:
            raise AssertionError("GIW holder lookup must not run when resolver ok")

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None
    lane, holder = target
    assert lane == "22760"
    assert holder["registration_id"] == reg
    assert holder["chat_url"] == chat


def test_scan_once_cse_subscription_registration_agent_id_enqueues_pending(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01TestHouseless"
    detail = create_thread(thread_id=None, slug="houseless-mailbox")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|wake probe",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    pending: list[dict[str, object]] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            pending.extend(rows)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(self, lane_thread_id: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    class StoreWithHolder(Store):
        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id != reg:
                return None
            return {
                "registration_id": reg,
                "chat_url": chat,
                "purpose": "houseless",
                "seat_state": "houseless",
            }

    scan_once(StoreWithHolder())  # type: ignore[arg-type]
    assert [int(p["turn_id"]) for p in pending] == [turn_id]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, agent FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"
    assert row["agent"] == "ulg-cortex-symbiosis"


def test_resolve_cse_wake_target_giw_error_fail_closed() -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    sub = {"surface": "cse", "agent_id": reg, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict:
            raise TimeoutError("giw down")

    assert resolve_cse_wake_target(Store(), sub, wake_thread="22760") is None  # type: ignore[arg-type]
    delivery = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread="22760",
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert delivery is not None
    _lane, holder = delivery
    assert holder.get("_giw_holder_lookup_failed") is True


def test_resolve_cse_wake_target_released_holder_no_wake() -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    sub = {"surface": "cse", "agent_id": reg, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict:
            return {"registration_id": reg, "seat_state": "released"}

    assert resolve_cse_wake_target(Store(), sub, wake_thread="22760") is None  # type: ignore[arg-type]
    delivery = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread="22760",
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert delivery is not None
    _, holder = delivery
    assert holder["seat_state"] == "released"


def test_resolve_cse_wake_target_live_registration_uses_giw_row() -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01TestLive"
    sub = {"surface": "cse", "agent_id": reg, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict:
            return {
                "registration_id": reg,
                "chat_url": chat,
                "seat_state": "driving",
                "lane_thread_id": "22760",
            }

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None
    lane, holder = target
    assert lane == "22760"
    assert holder["registration_id"] == reg
    assert holder["seat_state"] == "driving"


def test_resolve_cse_wake_target_houseless_provenance_route_no_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import (
        clear_houseless_resolve_memo,
        resolve_houseless_target,
    )

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg_id = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_route_no_episode"

    def _provenance_no_episode(
        registration_id: str, **_kwargs: object
    ) -> tuple[dict[str, object], bool]:
        return (
            {
                "registration_id": registration_id,
                "purpose": "houseless",
                "status": "active",
                "seat_open": False,
                "chat_url": chat,
                "state": "unresolved",
                "reason": "no_episode",
            },
            False,
        )

    monkeypatch.setattr(
        "cdp_ask.houseless_resolve._provenance_payload",
        _provenance_no_episode,
    )
    clear_houseless_resolve_memo()
    assert resolve_houseless_target(reg_id).ok is True
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        resolve_houseless_target,
    )

    sub = {"surface": "cse", "agent_id": reg_id, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            raise AssertionError("GIW lookup must not run when resolver ok")

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None
    _lane, holder = target
    assert holder["seat_state"] == "houseless"


def test_resolve_cse_wake_target_delivery_phase_bypasses_scan_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import (
        _RESOLVE_MEMO,
        HouselessResolution,
        clear_houseless_resolve_memo,
        resolve_houseless_target,
    )

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg_id = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_delivery_fresh"
    clear_houseless_resolve_memo()
    _RESOLVE_MEMO[reg_id] = HouselessResolution(ok=False, reason="no_row")

    def _provenance_ok(
        registration_id: str, **_kwargs: object
    ) -> tuple[dict[str, object], bool]:
        return (
            {
                "registration_id": registration_id,
                "purpose": "houseless",
                "status": "active",
                "seat_open": False,
                "chat_url": chat,
            },
            False,
        )

    monkeypatch.setattr(
        "cdp_ask.houseless_resolve._provenance_payload",
        _provenance_ok,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        resolve_houseless_target,
    )
    assert resolve_houseless_target(reg_id).reason == "no_row"
    sub = {"surface": "cse", "agent_id": reg_id, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

    target = resolve_cse_wake_target(  # type: ignore[arg-type]
        Store(), sub, wake_thread="22760", delivery_phase=True
    )
    assert target is not None
    _lane, holder = target
    assert holder["seat_state"] == "houseless"


def test_resolve_cse_wake_target_provenance_unreachable_delivery_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import resolve_houseless_target

    from bus_watch.endpoint_wake_dispatcher import (
        _GIW_HOLDER_LOOKUP_FAILED,
        resolve_cse_wake_target,
    )

    reg_id = "583b411df34247f9a48bc044b6518be3"

    def _provenance_down(*_a: object, **_k: object) -> tuple[None, bool]:
        return None, True

    monkeypatch.setattr(
        "cdp_ask.houseless_resolve._provenance_payload",
        _provenance_down,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        resolve_houseless_target,
    )
    sub = {"surface": "cse", "agent_id": reg_id, "thread_scope": "22760"}

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

    target = resolve_cse_wake_target(  # type: ignore[arg-type]
        Store(), sub, wake_thread="22760", delivery_phase=True
    )
    assert target is not None
    _lane, holder = target
    assert holder.get(_GIW_HOLDER_LOOKUP_FAILED) is True


def test_resolve_cse_wake_target_houseless_via_provenance_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg_id = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01HouselessTag"
    sub = {"surface": "cse", "agent_id": reg_id, "thread_scope": "22760"}
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=True, reason="ok", chat_url=chat),
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            raise AssertionError("GIW lookup must not run when resolver ok")

    assert resolve_cse_wake_target(Store(), sub, wake_thread="22760") is not None  # type: ignore[arg-type]
    lane, holder = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert lane == "22760"
    assert holder["seat_state"] == "houseless"
    assert holder["holder"] == f"houseless:{reg_id}"

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=False, reason="no_row"),
    )

    class EmptyStore:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

    assert resolve_cse_wake_target(EmptyStore(), sub, wake_thread="22760") is None  # type: ignore[arg-type]


def test_deliver_cse_houseless_followup_neutral_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import _deliver_cse

    captured: list[dict[str, Any]] = []

    reg = "583b411df34247f9a48bc044b6518be3"

    def _fire(*_args: object, **kwargs: Any) -> dict[str, Any]:
        captured.append(dict(kwargs))
        if kwargs.get("dry_run"):
            return {
                "ok": True,
                "dry_run": True,
                "registration_id": reg,
            }
        return {
            "ok": False,
            "error": "no_target",
            "registration_id": reg,
        }

    monkeypatch.setattr("bus_watch.cse_followup.fire_cse_followup", _fire)
    holder = {
        "registration_id": reg,
        "chat_url": "https://claude.ai/cowork/cse_01Houseless",
        "purpose": "houseless",
        "seat_state": "houseless",
    }
    rows = [{"thread_id": "22760", "turn_number": 2}]
    state, reason, _reg = _deliver_cse(rows, holder, target_lane="22760")
    assert captured[0]["purpose"] is None
    assert captured[0]["reattach"] is False
    assert captured[1]["purpose"] is None
    assert captured[1]["reattach"] is False
    assert state == "unknown"
    assert reason == "no_target"


def test_fire_due_wakes_giw_holder_by_registration_error_records_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

    recorded: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-giw-err",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "maestro",
                        "thread_id": "22760",
                        "turn_id": 1,
                        "turn_number": 2,
                        "decided_by_rule": "wake_cse_allowlist",
                    }
                ],
            }

        def holder_by_registration(self, _reg: str) -> dict:
            raise TimeoutError("giw down")

    reg = "583b411df34247f9a48bc044b6518be3"
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_target",
        lambda _store, _agent, _thread: (
            "exact",
            {"surface": "cse", "agent_id": reg, "id": 42, "thread_scope": "22760"},
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: recorded.append(kw),
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert recorded[0]["outcome"] == "unknown"
    assert recorded[0]["reason"] == "HolderLookupError"
    assert recorded[0]["subscription_id"] == 42


def test_fire_due_wakes_cse_registration_released_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

    recorded: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-released",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "maestro",
                        "thread_id": "22760",
                        "turn_id": 1,
                        "turn_number": 2,
                        "decided_by_rule": "wake_cse_allowlist",
                    }
                ],
            }

        def holder_by_registration(self, reg: str) -> dict:
            return {"registration_id": reg, "seat_state": "released"}

    reg = "583b411df34247f9a48bc044b6518be3"
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_target",
        lambda _store, _agent, _thread: (
            "exact",
            {"surface": "cse", "agent_id": reg, "id": 9},
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: recorded.append(kw),
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert recorded[0]["outcome"] == "skipped"
    assert recorded[0]["reason"] == "seat_released"


def test_fire_due_wakes_lane_holder_probe_error_records_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

    recorded: list[dict] = []

    class Store:
        def claim(self) -> dict:
            return {
                "wake_id": "wake-lane-err",
                "rows": [
                    {
                        "state": "claimed",
                        "agent": "maestro",
                        "thread_id": "18460",
                        "turn_id": 1,
                        "turn_number": 2,
                        "decided_by_rule": "wake_cse_allowlist",
                    }
                ],
            }

        def holder(self, _lane: str) -> dict:
            raise TimeoutError("giw down")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_target",
        lambda _store, _agent, _thread: (
            "exact",
            {"surface": "cse", "agent_id": "18460", "id": 11},
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.record",
        lambda store, **kw: recorded.append(kw),
    )
    fire_due_wakes(Store())  # type: ignore[arg-type]
    assert recorded[0]["outcome"] == "unknown"
    assert recorded[0]["reason"] == "TimeoutError"
    assert recorded[0]["subscription_id"] == 11


def test_scan_cse_no_open_seat_writes_scan_holder_skip(db: None) -> None:
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    reg = "583b411df34247f9a48bc044b6518be3"
    detail = create_thread(thread_id=None, slug="houseless-no-seat")
    assert detail is not None
    thread = str(detail["id"])
    sub = subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|wake probe",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    scanned: list[dict[str, object]] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            scanned.extend(rows)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 21:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(self, lane_thread_id: str) -> dict | None:
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._holder_for_cse_registration",
        lambda _store, rid, *, delivery_phase=False: None,
    )
    try:
        scan_once(Store())  # type: ignore[arg-type]
    finally:
        monkeypatch.undo()
    skip = [r for r in scanned if r.get("state") == "skipped"]
    assert len(skip) == 1
    assert skip[0]["reason"] == "no_route:scan_holder"
    sub_id = (sub.get("row") or {}).get("id")
    assert skip[0]["subscription_id"] == sub_id
    assert skip[0]["turn_id"] == turn_id
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, subscription_id FROM endpoint_wake_ledger "
            "WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "skipped"
    assert row["reason"] == "no_route:scan_holder"


def test_promote_scan_holder_skip_when_holder_appears(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    fresh_stamp = "2033-06-01 11:50:00"
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )

    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01HouselessPromote"
    detail = create_thread(thread_id=None, slug="houseless-promote")
    assert detail is not None
    thread = str(detail["id"])
    sub = subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|wake probe",
        body="body",
    )
    cursor = get_cursor()
    expected_hw = int(cursor["high_water_turn_id"])
    advance_scan(
        "endpoint-wake-dispatcher",
        expected_hw,
        turn_id,
        [
            {
                "agent": "ulg-cortex-symbiosis",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": turn_num,
                "state": "skipped",
                "reason": "no_route:scan_holder",
                "subscription_id": (sub.get("row") or {}).get("id"),
                "decided_by_rule": "wake.default",
            }
        ],
        now=fresh_stamp,
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            (fresh_stamp, turn_id),
        )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": thread,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    _promote_scan_holder_skips_safe(Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"
    assert row["reason"] is None


def test_stale_cse_no_holder_skips_scan_holder_tombstone(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    monkeypatch.setenv("ENDPOINT_WAKE_CATCHUP_MAX_AGE_S", "900")
    from datetime import UTC, datetime

    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now,
    )
    reg = "583b411df34247f9a48bc044b6518be3"
    detail = create_thread(thread_id=None, slug="houseless-stale-skip")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|stale holder skip",
        body="body",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            ("2020-01-01 00:00:00", turn_id),
        )
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at, created_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 21:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(self, lane_thread_id: str) -> dict | None:
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._holder_for_cse_registration",
        lambda _store, _rid, *, delivery_phase=False: None,
    )
    scan_once(Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is None


def test_promote_skips_stale_scan_holder_tombstone(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5 — fresh ledger row + turn older than catchup max_age expires, not promotes."""
    from datetime import UTC, datetime

    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    monkeypatch.setenv("ENDPOINT_WAKE_CATCHUP_MAX_AGE_S", "900")
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_ledger_stamp = "2033-06-01 11:50:00"
    stale_turn_stamp = "2020-01-01 00:00:00"
    reg = "583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_01HouselessPromoteStale"
    detail = create_thread(thread_id=None, slug="houseless-promote-stale")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|stale promote",
        body="body",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            (stale_turn_stamp, turn_id),
        )
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "finished_at, decided_by_rule) "
            "VALUES (?,?,?,?, 'skipped', 'no_route:scan_holder', ?, ?, 'wake.default')",
            (
                "ulg-cortex-symbiosis",
                thread,
                turn_id,
                turn_num,
                fresh_ledger_stamp,
                fresh_ledger_stamp,
            ),
        )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": thread,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    _promote_scan_holder_skips_safe(Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "skipped"
    assert row["reason"] == "no_route:scan_holder_expired"


def _insert_scan_holder_tombstone(
    *,
    agent: str,
    thread_id: str,
    turn_id: int,
    turn_number: int,
    created_at: str,
    subscription_id: int | None = None,
) -> int:
    from agent_bus_store.db.connection import write_connect

    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            (created_at, turn_id),
        )
        cur = conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "finished_at, decided_by_rule, subscription_id) "
            "VALUES (?,?,?,?, 'skipped', 'no_route:scan_holder', ?, ?, 'wake.test', ?)",
            (
                agent,
                thread_id,
                turn_id,
                turn_number,
                created_at,
                created_at,
                subscription_id,
            ),
        )
        return int(cur.lastrowid)


def test_promote_scan_holder_starvation_ac1(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1 — ledger-stale tombstones expire; fresh routable row promotes in one pass."""
    from datetime import UTC, datetime

    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch import endpoint_wake_dispatcher as dispatch_mod
    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    dispatch_mod._scan_holder_group_cursor = None
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    reg = "ac1reg583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac1"
    fresh_stamp = "2033-06-01 11:50:00"
    stale_stamp = "2020-01-01 00:00:00"
    detail = create_thread(thread_id=None, slug="ac1-routable")
    assert detail is not None
    house = str(detail["id"])
    sub = subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=house,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    sub_id = int((sub.get("row") or {}).get("id") or 0)
    for i in range(60):
        lane = create_thread(thread_id=None, slug=f"ac1-stale-{i}")
        assert lane is not None
        tid = str(lane["id"])
        turn_id, _ts, turn_num, _c = insert_turn(
            thread=tid,
            from_agent="houseless-driver",
            to_agent="ulg-cortex-symbiosis",
            subject=f"QUESTION|stale {i}",
            body="body",
        )
        _insert_scan_holder_tombstone(
            agent="ulg-cortex-symbiosis",
            thread_id=tid,
            turn_id=turn_id,
            turn_number=turn_num,
            created_at=stale_stamp,
        )
    fresh_turn_id, _ts, fresh_num, _c = insert_turn(
        thread=house,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|fresh routable",
        body="body",
    )
    fresh_ledger_id = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=house,
        turn_id=fresh_turn_id,
        turn_number=fresh_num,
        created_at=fresh_stamp,
        subscription_id=sub_id,
    )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": house,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    _promote_scan_holder_skips_safe(Store())  # type: ignore[arg-type]
    with connect() as conn:
        fresh = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE id=?",
            (fresh_ledger_id,),
        ).fetchone()
        stale_rows = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger "
            "WHERE reason IN ('no_route:scan_holder', 'no_route:scan_holder_expired') "
            "AND id != ?",
            (fresh_ledger_id,),
        ).fetchall()
    assert fresh is not None
    assert fresh["state"] == "pending"
    assert fresh["reason"] is None
    assert len(stale_rows) == 60
    assert all(r["reason"] == "no_route:scan_holder_expired" for r in stale_rows)


def test_promote_scan_holder_fresh_crowding_ac2(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2 — routable group last in id order promotes within three passes."""
    from datetime import UTC, datetime

    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch import endpoint_wake_dispatcher as dispatch_mod
    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    dispatch_mod._scan_holder_group_cursor = None
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_stamp = "2033-06-01 11:50:00"
    reg = "ac2reg583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac2"
    for i in range(45):
        lane = create_thread(thread_id=None, slug=f"ac2-unroutable-{i}")
        assert lane is not None
        tid = str(lane["id"])
        turn_id, _ts, turn_num, _c = insert_turn(
            thread=tid,
            from_agent="houseless-driver",
            to_agent="ulg-cortex-symbiosis",
            subject=f"QUESTION|u{i}",
            body="body",
        )
        _insert_scan_holder_tombstone(
            agent="ulg-cortex-symbiosis",
            thread_id=tid,
            turn_id=turn_id,
            turn_number=turn_num,
            created_at=fresh_stamp,
        )
    routable_lane = create_thread(thread_id=None, slug="ac2-routable")
    assert routable_lane is not None
    routable_tid = str(routable_lane["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=routable_tid,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    routable_turn_id, _ts, routable_num, _c = insert_turn(
        thread=routable_tid,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|routable",
        body="body",
    )
    routable_ledger = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=routable_tid,
        turn_id=routable_turn_id,
        turn_number=routable_num,
        created_at=fresh_stamp,
    )

    resolve_counts: dict[tuple[str, str], int] = {}

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            key = (agent, thread_id)
            resolve_counts[key] = resolve_counts.get(key, 0) + 1
            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": routable_tid,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    store = Store()  # type: ignore[assignment]
    promoted = False
    for _ in range(3):
        _promote_scan_holder_skips_safe(store)
        with connect() as conn:
            row = conn.execute(
                "SELECT state FROM endpoint_wake_ledger WHERE id=?",
                (routable_ledger,),
            ).fetchone()
        if row and row["state"] == "pending":
            promoted = True
            break
    assert promoted
    for key, count in resolve_counts.items():
        assert count <= 3, f"group {key} resolved {count} times"


def test_promote_scan_holder_no_http_under_ticket_ac3(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3 — no store I/O while write_connect is open; at most two write tickets per pass."""
    from contextlib import contextmanager
    from datetime import UTC, datetime

    from agent_bus_store.db import connection as conn_mod
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch import endpoint_wake_dispatcher as dispatch_mod
    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    dispatch_mod._scan_holder_group_cursor = None
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_stamp = "2033-06-01 11:50:00"
    reg = "ac3reg583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac3"
    detail = create_thread(thread_id=None, slug="ac3-http-guard")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|ac3",
        body="body",
    )
    _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=thread,
        turn_id=turn_id,
        turn_number=turn_num,
        created_at=fresh_stamp,
    )
    ticket_open = {"flag": False}
    write_entries = {"n": 0}
    real_write_connect = conn_mod.write_connect

    @contextmanager
    def tracking_write_connect(*_a, **_k):  # type: ignore[no-untyped-def]
        write_entries["n"] += 1
        ticket_open["flag"] = True
        try:
            with real_write_connect(*_a, **_k) as c:
                yield c
        finally:
            ticket_open["flag"] = False

    monkeypatch.setattr(conn_mod, "write_connect", tracking_write_connect)
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_wake_ledger.write_connect",
        tracking_write_connect,
    )
    resolve_calls_under_ticket = {"n": 0}
    from cdp_ask.houseless_resolve import HouselessResolution

    def _tracking_resolve(_reg: str, **_: object) -> HouselessResolution:
        if ticket_open["flag"]:
            resolve_calls_under_ticket["n"] += 1
        return HouselessResolution(ok=False, reason="purpose_not_houseless")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        _tracking_resolve,
    )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            assert not ticket_open["flag"], "subscription under write ticket"
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            assert not ticket_open["flag"], "holder lookup under write ticket"
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": thread,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            assert not ticket_open["flag"]
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    _promote_scan_holder_skips_safe(Store())  # type: ignore[arg-type]
    assert write_entries["n"] <= 2
    assert resolve_calls_under_ticket["n"] == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"


def test_resolve_cse_wake_target_houseless_resolver_wins_over_giw_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "ac2houseless583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac2_houseless"
    sub = {"surface": "cse", "agent_id": reg, "thread_scope": "22760"}
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=True, reason="ok", chat_url=chat),
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict:
            return {
                "registration_id": registration_id,
                "chat_url": "https://claude.ai/cowork/cse_driving",
                "seat_state": "driving",
                "lane_thread_id": "99999",
            }

    _lane, holder = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert holder["seat_state"] == "houseless"
    assert holder["chat_url"] == chat


@pytest.mark.parametrize(
    "fail_reason",
    [
        "no_row",
        "purpose_not_houseless",
        "status_unlisted",
        "seat_open",
        "no_chat_url",
        "cdp_ask_unreachable",
    ],
)
def test_resolve_cse_wake_target_houseless_resolver_fail_closed(
    monkeypatch: pytest.MonkeyPatch, fail_reason: str
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    sub = {"surface": "cse", "agent_id": reg, "thread_scope": "22760"}
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda _reg, **_: HouselessResolution(ok=False, reason=fail_reason),
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

    if fail_reason == "purpose_not_houseless":
        assert (
            resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
            is None
        )
        return
    target = resolve_cse_wake_target(
        Store(), sub, wake_thread="22760", delivery_phase=True
    )
    assert target is not None
    _lane, holder = target
    assert str(holder.get("_houseless_no_route_reason") or "").startswith(
        f"houseless_{fail_reason}"
    )


def _thread_scoped_cse_sub(agent_id: str, thread: str = "22760") -> dict[str, Any]:
    return {
        "surface": "cse",
        "scope_kind": "thread",
        "agent_id": agent_id,
        "thread_scope": thread,
        "id": 99,
        "state": "subscribed",
    }


def _driving_holder(
    registration_id: str, *, lane: str = "22760", seat_state: str = "driving"
) -> dict[str, Any]:
    return {
        "registration_id": registration_id,
        "lane_thread_id": lane,
        "seat_state": seat_state,
        "chat_url": f"https://claude.ai/cowork/cse_{registration_id[:8]}",
    }


def test_cse_wake_thread_holder_bus_successor_skips_giw_by_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    pred = "pred583b411df34247f9a48bc044b6518be3"
    succ = "succ583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(pred, thread)

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: succ,
    )

    class Store:
        holder_calls: list[str] = []

        def holder(self, lane_thread_id: str, **kwargs: object) -> None:
            Store.holder_calls.append(lane_thread_id)
            raise AssertionError("GIW by-thread must not run when bus is live")

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == pred:
                return _driving_holder(pred, seat_state="released")
            if registration_id == succ:
                return _driving_holder(succ)
            return None

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    lane, holder = target
    assert lane == thread
    assert holder["registration_id"] == succ
    assert Store.holder_calls == []


def test_cse_wake_thread_holder_bus_wins_over_giw_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    pred = "pred583b411df34247f9a48bc044b6518be3"
    succ = "succ583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(pred, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: succ,
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            assert lane_thread_id == thread
            return _driving_holder(pred)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == succ:
                return _driving_holder(succ)
            if registration_id == pred:
                return _driving_holder(pred)
            return None

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == succ


def test_cse_wake_thread_holder_giw_when_bus_none_emits_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    succ = "succ583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub("stale583b411df34247f9a48bc044b6518be3", thread)
    emitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            return _driving_holder(succ)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == succ:
                return _driving_holder(succ)
            return {"registration_id": registration_id, "seat_state": "released"}

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == succ
    assert len(emitted) == 1
    assert emitted[0]["chosen_source"] == "giw_by_thread"
    assert emitted[0]["giw_by_thread_registration"] == succ

    fire_target = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert fire_target is not None
    assert fire_target[1]["registration_id"] == succ


def test_cse_wake_thread_holder_bus_not_live_giw_live_event_both_regs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    emitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            return _driving_holder(giw_reg)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == bus_reg:
                return {"registration_id": bus_reg, "seat_state": "released"}
            if registration_id == giw_reg:
                return _driving_holder(giw_reg)
            return None

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == giw_reg
    assert emitted[0]["bus_registration"] == bus_reg
    assert emitted[0]["giw_by_thread_registration"] == giw_reg

    fire_target = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert fire_target is not None
    assert fire_target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_giw_error_falls_back_agent_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    agent_reg = "agent583b411df34247f9a48bc044b6518be3"
    thread = "22760"
    sub = _thread_scoped_cse_sub(agent_reg, thread)
    emitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> None:
            raise TimeoutError("giw slow")

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == agent_reg:
                return _driving_holder(agent_reg)
            return None

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == agent_reg
    assert emitted[0]["giw_by_thread_error"] == "TimeoutError"
    assert emitted[0]["chosen_source"] == "subscription_agent_id"

    target2 = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target2 is not None

    fire_target = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert fire_target is not None
    assert fire_target[1]["registration_id"] == agent_reg


def test_cse_wake_thread_holder_no_live_same_skip_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    dead = "dead583b411df34247f9a48bc044b6518be3"
    sub = _thread_scoped_cse_sub(dead)

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: dead,
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            return _driving_holder(dead, seat_state="released")

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            return {"registration_id": registration_id, "seat_state": "released"}

    assert resolve_cse_wake_target(Store(), sub, wake_thread="22760") is None  # type: ignore[arg-type]
    delivery = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread="22760",
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert delivery is not None
    assert delivery[1]["seat_state"] == "released"


def test_cse_wake_thread_holder_agent_scope_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    sub = {
        "surface": "cse",
        "scope_kind": "agent",
        "agent_id": reg,
        "thread_scope": "22760",
    }
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: "should-never-read",
    )

    class Store:
        def holder(self, **_kwargs: object) -> None:
            raise AssertionError("thread-scoped path must not run")

        def holder_by_registration(self, registration_id: str) -> dict[str, Any]:
            return _driving_holder(registration_id)

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == reg


def test_cse_wake_thread_holder_bus_skipped_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    monkeypatch.delenv("AGENT_BUS_DB_PATH", raising=False)
    monkeypatch.setattr(
        "agent_bus_store.db.cse_associations.get_current_cse",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("get_current_cse must not run when env unset")
        ),
    )
    reg = "583b411df34247f9a48bc044b6518be3"
    sub = _thread_scoped_cse_sub(reg)

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict[str, Any]:
            return _driving_holder(registration_id)

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None


def test_cse_wake_thread_holder_memo_one_bus_and_giw_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        clear_cse_wake_thread_holder_memo,
        resolve_cse_wake_target,
    )

    thread = "22760"
    dead_bus = "dead583b411df34247f9a48bc044b6518be3"
    succ = "succ583b411df34247f9a48bc044b6518be4"
    bus_reads: list[str] = []
    giw_reads: list[str] = []

    def _bus(t: str) -> str | None:
        bus_reads.append(t)
        return dead_bus

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration", _bus
    )
    sub = _thread_scoped_cse_sub("stale583b411df34247f9a48bc044b6518be3", thread)

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            giw_reads.append(lane_thread_id)
            return _driving_holder(succ)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == dead_bus:
                return {"registration_id": dead_bus, "seat_state": "released"}
            if registration_id == succ:
                return _driving_holder(succ)
            return None

    store = Store()  # type: ignore[arg-type]
    clear_cse_wake_thread_holder_memo(store)  # type: ignore[arg-type]
    assert resolve_cse_wake_target(store, sub, wake_thread=thread) is not None
    assert resolve_cse_wake_target(store, sub, wake_thread=thread) is not None
    assert bus_reads == [thread]
    assert giw_reads == [thread]


def _giw_live_store(giw_reg: str, thread: str) -> type:
    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            assert lane_thread_id == thread
            return _driving_holder(giw_reg)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == giw_reg:
                return _driving_holder(giw_reg)
            return None

    return Store


def test_cse_wake_thread_holder_bus_no_route_falls_through_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: (
            HouselessResolution(ok=False, reason="no_row")
            if reg == bus_reg
            else HouselessResolution(ok=False, reason="purpose_not_houseless")
        ),
    )
    store_factory = _giw_live_store(giw_reg, thread)
    target = resolve_cse_wake_target(store_factory(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_bus_no_route_falls_through_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: (
            HouselessResolution(ok=False, reason="no_row")
            if reg == bus_reg
            else HouselessResolution(ok=False, reason="purpose_not_houseless")
        ),
    )
    store_factory = _giw_live_store(giw_reg, thread)
    target = resolve_cse_wake_target(
        store_factory(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert target is not None
    assert target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_bus_lookup_failed_falls_through_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: HouselessResolution(
            ok=False,
            reason=(
                "provenance_unreachable" if reg == bus_reg else "purpose_not_houseless"
            ),
        ),
    )

    store_factory = _giw_live_store(giw_reg, thread)
    target = resolve_cse_wake_target(store_factory(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_bus_lookup_failed_falls_through_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: HouselessResolution(
            ok=False,
            reason=(
                "provenance_unreachable" if reg == bus_reg else "purpose_not_houseless"
            ),
        ),
    )

    store_factory = _giw_live_store(giw_reg, thread)
    target = resolve_cse_wake_target(
        store_factory(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert target is not None
    assert target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_p7_emit_on_agent_id_fallback_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    agent_reg = "agent583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(agent_reg, thread)
    emitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: (
            HouselessResolution(ok=False, reason="no_row")
            if reg == bus_reg
            else HouselessResolution(ok=False, reason="purpose_not_houseless")
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == agent_reg:
                return _driving_holder(agent_reg)
            return None

    target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == agent_reg
    assert len(emitted) == 1
    assert emitted[0]["chosen_source"] == "subscription_agent_id"
    assert emitted[0]["bus_registration"] == bus_reg


def test_cse_wake_thread_holder_p7_emit_on_agent_id_fallback_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cdp_ask.houseless_resolve import HouselessResolution

    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    agent_reg = "agent583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(agent_reg, thread)
    emitted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_houseless_target",
        lambda reg, **_: (
            HouselessResolution(ok=False, reason="no_row")
            if reg == bus_reg
            else HouselessResolution(ok=False, reason="purpose_not_houseless")
        ),
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == agent_reg:
                return _driving_holder(agent_reg)
            return None

    target = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert target is not None
    assert target[1]["registration_id"] == agent_reg
    assert len(emitted) == 1
    assert emitted[0]["chosen_source"] == "subscription_agent_id"


def test_cse_wake_thread_holder_bus_holder_lookup_once_per_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        _holder_for_cse_registration,
        resolve_cse_wake_target,
    )

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    agent_reg = "agent583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(agent_reg, thread)
    bus_lookups: list[str] = []
    real = _holder_for_cse_registration

    def _counting(
        store: object, registration_id: str, *, delivery_phase: bool = False
    ) -> dict[str, Any] | None:
        if registration_id == bus_reg:
            bus_lookups.append(registration_id)
        return real(store, registration_id, delivery_phase=delivery_phase)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._holder_for_cse_registration", _counting
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **_kw: None,
    )

    class Store:
        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == bus_reg:
                return {"registration_id": bus_reg, "seat_state": "released"}
            if registration_id == agent_reg:
                return _driving_holder(agent_reg)
            return None

    assert (
        resolve_cse_wake_target(Store(), sub, wake_thread=thread) is not None  # type: ignore[arg-type]
    )
    assert bus_lookups == [bus_reg]


def test_fire_due_wakes_cse_thread_holder_bus_released_giw_live_delivers(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import patch

    from agent_bus_store.db.cse_associations import associate_cse
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    pred = "pred583b411df34247f9a48bc044b6518be5"
    chat = "https://claude.ai/cowork/cse_bus"
    detail = create_thread(thread_id=None, slug="p9-fire-bus-giw")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=pred,
        bound_by="test",
    )
    associate_cse(
        thread_id=thread,
        cse_chat_url=chat,
        cse_registration_id=bus_reg,
        bound_by="test",
    )
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=thread,
        from_agent="driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|p9",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    emitted: list[dict[str, Any]] = []

    class ScanStore:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(self, lane_thread_id: str, **kwargs: object) -> dict | None:
            if lane_thread_id == thread:
                return _driving_holder(giw_reg, lane=thread)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == bus_reg:
                return {"registration_id": bus_reg, "seat_state": "released"}
            if registration_id == giw_reg:
                return _driving_holder(giw_reg, lane=thread)
            if registration_id == pred:
                return {"registration_id": pred, "seat_state": "released"}
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    scan_once(ScanStore())  # type: ignore[arg-type]
    claimed = claim_due_wakes(now="2020-01-01 20:00:01")
    assert claimed.get("wake_id")

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._observe_cse_wake_holder_resolution",
        lambda **kw: emitted.append(kw),
    )

    class FireStore(ScanStore):
        def claim(self) -> dict:
            return claimed

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload.get("reason") is not None else None,
                str(payload["route"]) if payload.get("route") is not None else None,
                payload.get("subscription_id"),  # type: ignore[arg-type]
                payload.get("cse_registration_id"),  # type: ignore[arg-type]
                now="2020-01-01 20:01:00",
            )

    def _cse_ok(_induction: str, _lane: str, **kwargs: object) -> dict[str, Any]:
        if kwargs.get("dry_run"):
            return {"ok": True, "dry_run": True, "registration_id": giw_reg}
        return {"ok": True, "registration_id": giw_reg, "send_verified": True}

    with patch("bus_watch.cse_followup.fire_cse_followup", side_effect=_cse_ok):
        fire_due_wakes(FireStore())  # type: ignore[arg-type]

    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE wake_id=?",
            (claimed["wake_id"],),
        ).fetchone()
    assert row is not None
    assert row["state"] == "delivered"
    assert emitted
    assert emitted[0]["chosen_source"] == "giw_by_thread"
    assert emitted[0]["bus_registration"] == bus_reg
    assert emitted[0]["giw_by_thread_registration"] == giw_reg


def test_cse_wake_thread_holder_fire_bus_released_giw_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    bus_reg = "bus583b411df34247f9a48bc044b6518be3"
    giw_reg = "giw583b411df34247f9a48bc044b6518be4"
    thread = "22760"
    sub = _thread_scoped_cse_sub(bus_reg, thread)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: bus_reg,
    )

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            return _driving_holder(giw_reg)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == bus_reg:
                return {"registration_id": bus_reg, "seat_state": "released"}
            if registration_id == giw_reg:
                return _driving_holder(giw_reg)
            return None

    scan_target = resolve_cse_wake_target(Store(), sub, wake_thread=thread)  # type: ignore[arg-type]
    fire_target = resolve_cse_wake_target(
        Store(),
        sub,
        wake_thread=thread,
        delivery_phase=True,  # type: ignore[arg-type]
    )
    assert scan_target is not None
    assert fire_target is not None
    assert scan_target[1]["registration_id"] == giw_reg
    assert fire_target[1]["registration_id"] == giw_reg


def test_cse_wake_thread_holder_wildcard_scope_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import resolve_cse_wake_target

    reg = "583b411df34247f9a48bc044b6518be3"
    sub = {
        "surface": "cse",
        "scope_kind": "thread",
        "agent_id": reg,
        "thread_scope": "*",
        "id": 1,
    }
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda _t: "never",
    )

    class Store:
        def holder(self, **_kwargs: object) -> None:
            raise AssertionError("thread holder path must not run for wildcard scope")

        def holder_by_registration(self, registration_id: str) -> dict[str, Any]:
            return _driving_holder(registration_id)

    target = resolve_cse_wake_target(Store(), sub, wake_thread="22760")  # type: ignore[arg-type]
    assert target is not None
    assert target[1]["registration_id"] == reg


def test_promote_scan_holder_sees_empty_memo_after_fire_residue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        _CSE_WAKE_THREAD_HOLDER_MEMO,
        scan_once,
    )

    memo_at_promote: list[dict[str, dict[str, Any]]] = []

    def _promote_probe(store: object) -> None:
        memo_at_promote.append(
            dict(getattr(store, _CSE_WAKE_THREAD_HOLDER_MEMO, {}))  # type: ignore[arg-type]
        )

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._promote_scan_holder_skips_safe",
        _promote_probe,
    )

    class Store:
        def cursor(self) -> dict[str, int]:
            return {"high_water_turn_id": 0}

        def since(self, *_a: object, **_k: object) -> list[dict[str, Any]]:
            return []

    store = Store()  # type: ignore[arg-type]
    setattr(
        store,
        _CSE_WAKE_THREAD_HOLDER_MEMO,
        {"22760": {"giw_by_thread_registration": "poison-from-fire"}},
    )
    scan_once(store)  # type: ignore[arg-type]
    assert memo_at_promote
    assert memo_at_promote[0] == {}


def test_cse_wake_thread_holder_memo_cleared_at_fire_due_wakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        _thread_holder_memo,
        clear_cse_wake_thread_holder_memo,
        fire_due_wakes,
        resolve_cse_wake_target,
    )

    thread = "22760"
    dead_bus = "dead583b411df34247f9a48bc044b6518be3"
    succ = "succ583b411df34247f9a48bc044b6518be4"
    bus_reads: list[str] = []
    giw_reads: list[str] = []
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._local_bus_cse_registration",
        lambda t: bus_reads.append(t) or dead_bus,
    )
    sub = _thread_scoped_cse_sub("stale583b411df34247f9a48bc044b6518be3", thread)

    class Store:
        def holder(self, lane_thread_id: str, **kwargs: object) -> dict[str, Any]:
            giw_reads.append(lane_thread_id)
            return _driving_holder(succ)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            if registration_id == dead_bus:
                return {"registration_id": dead_bus, "seat_state": "released"}
            if registration_id == succ:
                return _driving_holder(succ)
            return None

        def claim(self) -> dict[str, Any]:
            return {}

    store = Store()  # type: ignore[arg-type]
    clear_cse_wake_thread_holder_memo(store)  # type: ignore[arg-type]
    resolve_cse_wake_target(store, sub, wake_thread=thread)
    assert bus_reads == [thread]
    assert giw_reads == [thread]
    assert _thread_holder_memo(store)[thread]["giw_by_thread_registration"] == succ

    fire_due_wakes(store)  # type: ignore[arg-type]
    assert dict(getattr(store, "_cse_wake_thread_holder_memo", {})) == {}

    giw_reads.clear()
    bus_reads.clear()
    resolve_cse_wake_target(store, sub, wake_thread=thread, delivery_phase=True)
    assert bus_reads == [thread]
    assert giw_reads == [thread]


def test_scan_once_cse_thread_holder_bus_live_despite_stale_agent_id(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bus_store.db.cse_associations import associate_cse
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    pred = "pred583b411df34247f9a48bc044b6518be3"
    succ = "succ583b411df34247f9a48bc044b6518be4"
    chat = "https://claude.ai/cowork/cse_succ"
    detail = create_thread(thread_id=None, slug="hop-successor-scan")
    assert detail is not None
    thread = str(detail["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=pred,
        bound_by="test",
    )
    associate_cse(
        thread_id=thread,
        cse_chat_url=chat,
        cse_registration_id=succ,
        bound_by="test",
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=thread,
        from_agent="driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|wake",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    pending: list[dict[str, object]] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            pending.extend(rows)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(self, lane_thread_id: str, **kwargs: object) -> dict | None:
            return _driving_holder(pred)

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == succ:
                return _driving_holder(succ, lane=thread)
            if registration_id == pred:
                return {"registration_id": pred, "seat_state": "released"}
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    scan_once(Store())  # type: ignore[arg-type]
    assert [int(p["turn_id"]) for p in pending] == [turn_id]


def test_deliver_cse_houseless_resolver_precedence_followup_kw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import _deliver_cse

    captured: list[dict[str, Any]] = []
    reg = "583b411df34247f9a48bc044b6518be3"

    def _fire(*_args: object, **kwargs: Any) -> dict[str, Any]:
        captured.append(dict(kwargs))
        if kwargs.get("dry_run"):
            return {"ok": True, "dry_run": True, "registration_id": reg}
        return {"ok": False, "error": "no_target", "registration_id": reg}

    monkeypatch.setattr("bus_watch.cse_followup.fire_cse_followup", _fire)
    holder = {
        "registration_id": reg,
        "chat_url": "https://claude.ai/cowork/cse_syn",
        "purpose": "houseless",
        "seat_state": "houseless",
        "holder": f"houseless:{reg}",
    }
    rows = [{"thread_id": "22760", "turn_number": 2}]
    _deliver_cse(rows, holder, target_lane="22760")
    assert captured[0]["purpose"] is None
    assert captured[0]["reattach"] is False
    assert captured[0].get("houseless_delivery") is True


def test_promote_scan_holder_cas_ac4(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4 — CAS: concurrent claim prevents promotion."""
    from datetime import UTC, datetime

    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_wake_ledger import (
        list_scan_holder_candidates,
        promote_scan_holder_rows,
    )

    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_stamp = "2033-06-01 11:50:00"
    cutoff_stamp = "2033-06-01 11:45:00"
    detail = create_thread(thread_id=None, slug="ac4-cas")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|cas",
        body="body",
    )
    ledger_id = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=thread,
        turn_id=turn_id,
        turn_number=turn_num,
        created_at=fresh_stamp,
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='claimed', claimed_at=? WHERE id=?",
            (fresh_stamp, ledger_id),
        )
    n = promote_scan_holder_rows([ledger_id], [])
    assert n == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE id=?",
            (ledger_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "claimed"
    assert list_scan_holder_candidates(cutoff_stamp) == []


def test_promote_scan_holder_parent_route_ac6(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6 — parent-scope CSE route promotes when holder is live."""
    from datetime import UTC, datetime

    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch import endpoint_wake_dispatcher as dispatch_mod
    from bus_watch.endpoint_wake_dispatcher import _promote_scan_holder_skips_safe

    dispatch_mod._scan_holder_group_cursor = None
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_stamp = "2033-06-01 11:50:00"
    reg = "ac6reg583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac6"
    house = create_thread(thread_id=None, slug="ac6-house")
    worker = create_thread(thread_id=None, slug="ac6-worker")
    assert house is not None and worker is not None
    house_id = str(house["id"])
    worker_id = str(worker["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=house_id,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    turn_id, _ts, turn_num, _c = insert_turn(
        thread=worker_id,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|parent route",
        body="body",
    )
    ledger_id = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=worker_id,
        turn_id=turn_id,
        turn_number=turn_num,
        created_at=fresh_stamp,
    )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            if thread_id == worker_id:
                row = current_subscription(agent, house_id)
            else:
                row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                if thread_id == worker_id:
                    return ("parent", row)
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": house_id,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            if thread_id == worker_id:
                return house_id
            return None

    _promote_scan_holder_skips_safe(Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE id=?",
            (ledger_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"
    assert row["reason"] is None


def test_promote_scan_holder_fail_fast_ac7(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7 — resolver error stops the pass; cursor advances so later groups promote."""
    from datetime import UTC, datetime

    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    from bus_watch import endpoint_wake_dispatcher as dispatch_mod
    from bus_watch.endpoint_wake_dispatcher import (
        _promote_scan_holder_skips_safe,
        scan_once,
    )

    dispatch_mod._scan_holder_group_cursor = None
    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.time.time",
        lambda: fixed_now.timestamp(),
    )
    fresh_stamp = "2033-06-01 11:50:00"
    reg = "ac7reg583b411df34247f9a48bc044b6518be3"
    chat = "https://claude.ai/cowork/cse_ac7"
    fail_lane = create_thread(thread_id=None, slug="ac7-fail")
    routable_lane = create_thread(thread_id=None, slug="ac7-routable")
    assert fail_lane is not None and routable_lane is not None
    fail_tid = str(fail_lane["id"])
    routable_tid = str(routable_lane["id"])
    subscribe_endpoint(
        agent="ulg-cortex-symbiosis",
        thread_scope=routable_tid,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=reg,
        bound_by="test",
    )
    fail_turn_id, _ts, fail_num, _c = insert_turn(
        thread=fail_tid,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|fail",
        body="body",
    )
    fail_ledger = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=fail_tid,
        turn_id=fail_turn_id,
        turn_number=fail_num,
        created_at=fresh_stamp,
    )
    routable_turn_id, _ts, routable_num, _c = insert_turn(
        thread=routable_tid,
        from_agent="houseless-driver",
        to_agent="ulg-cortex-symbiosis",
        subject="QUESTION|routable",
        body="body",
    )
    routable_ledger = _insert_scan_holder_tombstone(
        agent="ulg-cortex-symbiosis",
        thread_id=routable_tid,
        turn_id=routable_turn_id,
        turn_number=routable_num,
        created_at=fresh_stamp,
    )
    group_fail = ("ulg-cortex-symbiosis", fail_tid)
    group_ok = ("ulg-cortex-symbiosis", routable_tid)

    resolve_order: list[tuple[str, str]] = []

    class PromoteStore:
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            key = (agent, thread_id)
            resolve_order.append(key)
            if key == group_fail:
                raise RuntimeError("resolver blow")
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            if registration_id == reg:
                return {
                    "registration_id": reg,
                    "chat_url": chat,
                    "seat_state": "driving",
                    "lane_thread_id": routable_tid,
                }
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    promote_store = PromoteStore()  # type: ignore[assignment]
    promoted = False
    for pass_idx in range(2):
        _promote_scan_holder_skips_safe(promote_store)
        with connect() as conn:
            row = conn.execute(
                "SELECT state FROM endpoint_wake_ledger WHERE id=?",
                (routable_ledger,),
            ).fetchone()
        if row and row["state"] == "pending":
            promoted = True
            assert pass_idx == 1
            break
    assert promoted
    assert resolve_order[0] == group_fail
    assert group_ok in resolve_order

    dispatch_mod._scan_holder_group_cursor = None
    scan_resolve_order: list[tuple[str, str]] = []

    class ScanStore:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            return []

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher", expected, new_hw, rows, now=fresh_stamp
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            key = (agent, thread_id)
            scan_resolve_order.append(key)
            if key == group_fail:
                raise RuntimeError("resolver blow")
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def holder_by_registration(self, registration_id: str) -> dict | None:
            return None

        def lane_parent(self, thread_id: str) -> str | None:
            return None

    scan_once(ScanStore())  # type: ignore[arg-type]
    assert scan_resolve_order == [group_fail]
    with connect() as conn:
        fail_row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE id=?",
            (fail_ledger,),
        ).fetchone()
    assert fail_row is not None
    assert fail_row["state"] == "skipped"


def _ledger_row(conn, turn_id: int) -> dict | None:  # noqa: ARG001
    row = conn.execute(
        "SELECT agent, state, reason, decided_by_rule FROM endpoint_wake_ledger "
        "WHERE turn_id=?",
        (turn_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _scan_drop_row(conn, turn_id: int, reason: str) -> dict | None:
    row = conn.execute(
        "SELECT agent, reason, rule_id FROM endpoint_wake_scan_drops "
        "WHERE turn_id=? AND reason=?",
        (turn_id, reason),
    ).fetchone()
    return dict(row) if row is not None else None


def _rewind_cursor_hw(hw: int) -> None:
    from agent_bus_store.db.connection import write_connect

    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=int(hw))


def test_scan_path1_suppress_unsubscribed_adds_no_ledger_item(db: None) -> None:
    turn = {
        "id": 99,
        "thread": "path1",
        "turn_number": 1,
        "from_agent": "orion",
        "to_agent": "orion",
        "subject": "DONE|noop",
    }

    class Store:
        def __init__(self) -> None:
            self.batch: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, _high: int) -> list[dict]:
            return [turn]

        def subscription(self, agent: str, thread: str) -> None:
            return None

        def scan(self, _high: int, _new_hw: int, rows: list[dict]) -> None:
            self.batch = list(rows)

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert store.batch == []
    assert not qualify(turn, cse=False).wake


def test_scan_store_batch_master_parity_path2_no_skips_in_scan(db: None) -> None:
    """store.scan payload matches master: no tombstones on path 2."""
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="parity-p2")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, _t_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="maestro",
        subject="QUESTION|parity",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET read_at=? WHERE id=?",
            ("2020-01-01 18:00:00", turn_id),
        )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    batches: list[list[dict]] = []

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            batches.append(list(rows))
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 12:00:01",
            )

        def holder(self, lane: str) -> dict:
            return {"registration_id": "reg-live", "seat_state": "driving"}

        def subscription(self, agent: str, thread_id: str):
            return (
                "cse",
                {
                    "surface": "cse",
                    "agent_id": thread_id,
                    "id": 1,
                    "state": "subscribed",
                },
            )

    scan_once(Store())  # type: ignore[arg-type]
    assert batches == [[]]
    with connect() as conn:
        assert _ledger_row(conn, turn_id) is None
        assert _scan_drop_row(conn, turn_id, "skipped:already_read") is not None


def test_three_pass_ledger_pending_path4_web_anthropic(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass3 rewind yields pending on ledger (master parity); drop recorded separately."""
    from agent_bus_store.db.connection import write_connect

    detail = create_thread(thread_id=None, slug="tp-p4")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, turn_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="web-anthropic",
        subject="QUESTION|tp4",
        body="b",
    )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 12:00:01",
            )

        def holder(self, lane: str) -> None:
            return None

        def lane_parent(self, thread_id: str) -> None:
            return None

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher.resolve_turn_address",
        lambda _t, _s: None,
    )
    monkeypatch.setattr(
        "bus_watch.endpoint_wake_dispatcher._display_seated",
        lambda _s, _t: False,
    )

    scan_once(Store())  # type: ignore[arg-type]
    _rewind_cursor_hw(turn_id - 1)

    class HolderStore(Store):
        def holder(self, lane: str) -> dict:
            return {"registration_id": "reg-live", "seat_state": "driving"}

    scan_once(HolderStore())  # type: ignore[arg-type]

    with connect() as conn:
        led = _ledger_row(conn, turn_id)
        assert led is not None
        assert led["state"] == "pending"
        drop = _scan_drop_row(conn, turn_id, "skipped:no_route_web_anthropic")
        assert drop is not None


def test_orion_path6_scan_drop_ledger_claimable(db: None) -> None:
    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.orion_wake_owner_adapter import (
        LedgerWakeDeliveryOwner,
        WakeClaimContext,
    )

    detail = create_thread(thread_id=None, slug="orion-p6")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, turn_n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent="orion",
        subject="ANSWER|probe",
        body="b",
    )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

    class Store:
        def cursor(self) -> dict:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 12:00:01",
            )

        def subscription(self, agent: str, thread_id: str) -> None:
            return None

    scan_once(Store())  # type: ignore[arg-type]
    with connect() as conn:
        assert _ledger_row(conn, turn_id) is None
        drop = _scan_drop_row(conn, turn_id, "skipped:no_route")
        assert drop is not None
        assert drop["agent"] == "orion"

    claim = WakeClaimContext(
        wake_id="wake-orion-p6",
        agent="orion",
        thread_id=thread,
        turn_ids=(turn_id,),
        turn_numbers=(turn_n,),
        endpoint_url="https://chatgpt.com/c/test",
    )
    owner = LedgerWakeDeliveryOwner()
    acquired = owner._acquire_direct_claim(claim)
    assert acquired.get("ok") is False
    assert acquired.get("reason") == "delivery_unbound"
    with connect() as conn:
        led = _ledger_row(conn, turn_id)
        assert led is not None
        assert led["state"] == "claimed"


def test_scan_drops_paths_2_to_7_unique_reasons(db: None) -> None:
    """After one scan each, drop table holds one row per path reason (isolated threads)."""
    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    expected = {
        2: "skipped:already_read",
        3: "skipped:display_seated",
        4: "skipped:no_route_web_anthropic",
        5: "skipped:stale_catchup_cse_no_target",
        6: "skipped:no_route",
        7: "skipped:stale_catchup",
    }

    from datetime import UTC, datetime

    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()

    configs = {
        2: dict(
            slug="drop-p2",
            to_agent="maestro",
            subject="QUESTION|p2",
            read_at="2020-01-01 00:00:00",
        ),
        3: dict(
            slug="drop-p3",
            to_agent="web-anthropic",
            subject="QUESTION|p3",
            patch_display=True,
        ),
        4: dict(
            slug="drop-p4",
            to_agent="web-anthropic",
            subject="QUESTION|p4",
            no_holder=True,
        ),
        5: dict(
            slug="drop-p5",
            to_agent="ulg-cortex-symbiosis",
            subject="QUESTION|p5",
            stale=True,
            cse_sub=True,
            cse_no_holder=True,
        ),
        6: dict(
            slug="drop-p6",
            to_agent="cursor",
            subject="QUESTION|p6",
            no_sub=True,
        ),
        7: dict(
            slug="drop-p7",
            to_agent="maestro",
            subject="QUESTION|p7",
            stale=True,
        ),
    }

    turn_ids: dict[int, int] = {}

    for path, cfg in configs.items():
        detail = create_thread(thread_id=None, slug=str(cfg["slug"]))
        thread = str(detail["id"])
        turn_id, _ts, _n, _c = insert_turn(
            thread=thread,
            from_agent="grok-bot-gc1",
            to_agent=str(cfg["to_agent"]),
            subject=str(cfg["subject"]),
            body="b",
        )
        turn_ids[path] = turn_id
        if cfg.get("read_at"):
            with write_connect() as conn:
                conn.execute(
                    "UPDATE turns SET read_at=? WHERE id=?",
                    (cfg["read_at"], turn_id),
                )
        if cfg.get("stale"):
            with write_connect() as conn:
                conn.execute(
                    "UPDATE turns SET created_at=? WHERE id=?",
                    ("2020-01-01 00:00:00", turn_id),
                )
        if cfg.get("cse_sub"):
            subscribe_endpoint(
                agent="ulg-cortex-symbiosis",
                thread_scope=thread,
                surface="cse",
                display="cse",
                title="cowork",
                agent_id="583b411df34247f9a48bc044b6518be3",
                bound_by="test",
            )
        get_cursor()
        with write_connect() as conn:
            rewind_wake_cursor_for_test(conn, CONSUMER, to=0)

        class Store:
            def __init__(self, thread_id: str, path_cfg: dict) -> None:
                self.thread_id = thread_id
                self.path_cfg = path_cfg

            def cursor(self) -> dict:
                return get_cursor()

            def since(self, after_id: int, limit: int = 50) -> list[dict]:
                with connect() as c:
                    rows = c.execute(
                        "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                        "body, read_at, created_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                        (after_id, limit),
                    ).fetchall()
                return [dict(r) for r in rows]

            def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
                return advance_scan(
                    "endpoint-wake-dispatcher",
                    expected,
                    new_hw,
                    rows,
                    now="2033-06-01 12:00:01",
                )

            def holder(self, lane: str) -> dict | None:
                if self.path_cfg.get("no_holder"):
                    return None
                return {"registration_id": "reg-live", "seat_state": "driving"}

            def lane_parent(self, thread_id: str) -> None:
                return None

            def subscription(
                self, agent: str, thread_id: str, raw_to: str | None = None
            ):
                if self.path_cfg.get("no_sub"):
                    return None
                if self.path_cfg.get("cse_sub"):
                    from agent_bus_store.db.endpoint_subscriptions import (
                        current_subscription,
                    )

                    row = current_subscription(agent, thread_id)
                    if row and row.get("state") == "subscribed":
                        return ("exact", row)
                if agent in {"maestro", "cursor"} and not self.path_cfg.get("cse_sub"):
                    if not self.path_cfg.get("no_holder"):
                        return (
                            "cse",
                            {
                                "surface": "cse",
                                "agent_id": thread_id,
                                "id": 1,
                                "state": "subscribed",
                            },
                        )
                return None

            def holder_by_registration(self, registration_id: str) -> None:
                return None

        import bus_watch.endpoint_wake_dispatcher as ew

        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(ew.time, "time", lambda: fixed_now)
            if cfg.get("patch_display"):
                mp.setattr(ew, "_display_seated", lambda _s, _t: True)
            if cfg.get("no_holder"):
                mp.setattr(
                    "bus_watch.endpoint_wake_dispatcher.resolve_turn_address",
                    lambda _t, _s: None,
                )
                mp.setattr(ew, "_display_seated", lambda _s, _t: False)
            if cfg.get("cse_no_holder"):
                mp.setattr(
                    ew,
                    "_holder_for_cse_registration",
                    lambda _store, rid, *, delivery_phase=False: None,
                )
            scan_once(Store(thread, cfg))  # type: ignore[arg-type]
        finally:
            mp.undo()

    with connect() as conn:
        for path, reason in expected.items():
            tid = turn_ids[path]
            drop = _scan_drop_row(conn, tid, reason)
            assert drop is not None, (path, reason, tid)
            assert _ledger_row(conn, tid) is None


def _ledger_rows_for_turn(conn, turn_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT agent, turn_id, state, reason FROM endpoint_wake_ledger "
        "WHERE turn_id=? ORDER BY id",
        (turn_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _scan_drop_rows_for_turn(conn, turn_id: int) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT agent, turn_id, reason, COUNT(*) AS count "
            "FROM endpoint_wake_scan_drops WHERE turn_id=? "
            "GROUP BY agent, turn_id, reason ORDER BY reason",
            (turn_id,),
        ).fetchall()
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise
    return [dict(r) for r in rows]


def _three_pass_path_setup(
    path: int,
    *,
    slug: str,
) -> tuple[str, int, dict]:
    """Return thread, turn_id, and path-local knobs for passes 1–3."""
    from datetime import UTC, datetime

    from agent_bus_store.db.connection import write_connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    fixed_now = datetime(2033, 6, 1, 12, 0, 0, tzinfo=UTC).timestamp()

    configs: dict[int, dict] = {
        2: dict(
            to_agent="maestro",
            subject="QUESTION|tp2",
            read_at="2020-01-01 00:00:00",
        ),
        3: dict(
            to_agent="web-anthropic",
            subject="QUESTION|tp3",
            display_pass1=True,
        ),
        4: dict(
            to_agent="web-anthropic",
            subject="QUESTION|tp4",
            no_holder_pass1=True,
        ),
        5: dict(
            to_agent="ulg-cortex-symbiosis",
            subject="QUESTION|tp5",
            stale=True,
            cse_sub=True,
            cse_no_holder_pass1=True,
            fixed_now=fixed_now,
        ),
        6: dict(
            to_agent="cursor",
            subject="QUESTION|tp6",
            no_sub_pass1=True,
        ),
        7: dict(
            to_agent="maestro",
            subject="QUESTION|tp7",
            stale=True,
            fixed_now=fixed_now,
        ),
    }
    cfg = dict(configs[path])
    detail = create_thread(thread_id=None, slug=slug)
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, _n, _c = insert_turn(
        thread=thread,
        from_agent="grok-bot-gc1",
        to_agent=str(cfg["to_agent"]),
        subject=str(cfg["subject"]),
        body="b",
    )
    if cfg.get("read_at"):
        with write_connect() as conn:
            conn.execute(
                "UPDATE turns SET read_at=? WHERE id=?",
                (cfg["read_at"], turn_id),
            )
    if cfg.get("stale"):
        with write_connect() as conn:
            conn.execute(
                "UPDATE turns SET created_at=? WHERE id=?",
                ("2020-01-01 00:00:00", turn_id),
            )
    if cfg.get("cse_sub"):
        subscribe_endpoint(
            agent="ulg-cortex-symbiosis",
            thread_scope=thread,
            surface="cse",
            display="cse",
            title="cowork",
            agent_id="583b411df34247f9a48bc044b6518be3",
            bound_by="test",
        )
    get_cursor()
    with write_connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    cfg["thread"] = thread
    cfg["turn_id"] = turn_id
    return thread, turn_id, cfg


def _three_pass_apply_pass2_db(path: int, turn_id: int, cfg: dict) -> None:
    from agent_bus_store.db.connection import write_connect

    if path == 2:
        with write_connect() as conn:
            conn.execute("UPDATE turns SET read_at=NULL WHERE id=?", (turn_id,))
    elif path in (5, 7):
        with write_connect() as conn:
            conn.execute(
                "UPDATE turns SET created_at=? WHERE id=?",
                ("2033-06-01T11:59:00Z", turn_id),
            )


class _ThreePassProbeStore:
    def __init__(self, cfg: dict, *, phase: int) -> None:
        self.cfg = cfg
        self.phase = phase

    def cursor(self) -> dict:
        return get_cursor()

    def since(self, after_id: int, limit: int = 50) -> list[dict]:
        with connect() as c:
            rows = c.execute(
                "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                "body, read_at, created_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
        return advance_scan(
            "endpoint-wake-dispatcher",
            expected,
            new_hw,
            rows,
            now="2033-06-01 12:00:01",
        )

    def holder(self, lane: str) -> dict | None:
        if self.cfg.get("no_holder_pass1") and self.phase == 1:
            return None
        if self.cfg.get("cse_no_holder_pass1") and self.phase == 1:
            return None
        return {"registration_id": "reg-live", "seat_state": "driving"}

    def lane_parent(self, thread_id: str) -> None:
        return None

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        if self.cfg.get("no_sub_pass1") and self.phase == 1:
            return None
        if self.cfg.get("cse_sub"):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
        if agent in {"maestro", "cursor"} and not self.cfg.get("cse_sub"):
            return (
                "cse",
                {
                    "surface": "cse",
                    "agent_id": thread_id,
                    "id": 1,
                    "state": "subscribed",
                },
            )
        return None

    def holder_by_registration(self, registration_id: str) -> dict | None:
        if self.cfg.get("cse_no_holder_pass1") and self.phase == 1:
            return None
        return {"registration_id": registration_id, "seat_state": "driving"}


def _three_pass_probe_monkeypatch(
    mp: pytest.MonkeyPatch,
    path: int,
    cfg: dict,
    *,
    phase: int,
) -> None:
    import bus_watch.endpoint_wake_dispatcher as ew

    if cfg.get("fixed_now"):
        mp.setattr(ew.time, "time", lambda: cfg["fixed_now"])
    if path == 3:
        seated = bool(cfg.get("display_pass1") and phase == 1)
        mp.setattr(ew, "_display_seated", lambda _s, _t: seated)
    if path == 4:
        mp.setattr(ew, "_display_seated", lambda _s, _t: False)
        if cfg.get("no_holder_pass1") and phase == 1:
            mp.setattr(
                "bus_watch.endpoint_wake_dispatcher.resolve_turn_address",
                lambda _t, _s: None,
            )
    if path == 5 and cfg.get("cse_no_holder_pass1") and phase == 1:
        mp.setattr(
            ew,
            "_holder_for_cse_registration",
            lambda _store, rid, *, delivery_phase=False: None,
        )


def run_three_pass_path_probe(
    path: int,
    monkeypatch: pytest.MonkeyPatch,
    *,
    slug: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """Three-pass probe; returns (final ledger rows, all scan_drop rows for turn)."""
    _thread, turn_id, cfg = _three_pass_path_setup(
        path, slug=slug or f"three-pass-p{path}"
    )
    drop_trace: list[dict] = []
    for phase in (1, 2, 3):
        mp = pytest.MonkeyPatch()
        try:
            _three_pass_probe_monkeypatch(mp, path, cfg, phase=phase)
            if phase == 2:
                _three_pass_apply_pass2_db(path, turn_id, cfg)
            store = _ThreePassProbeStore(cfg, phase=phase if phase < 3 else 2)
            if phase == 1:
                scan_once(store)  # type: ignore[arg-type]
            elif phase == 2:
                scan_once(store)  # type: ignore[arg-type]
            else:
                _rewind_cursor_hw(turn_id - 1)
                scan_once(store)  # type: ignore[arg-type]
        finally:
            mp.undo()
        with connect() as conn:
            drop_trace.extend(_scan_drop_rows_for_turn(conn, turn_id))
    with connect() as conn:
        ledger = _ledger_rows_for_turn(conn, turn_id)
    return ledger, drop_trace


# Observed on detached master @ 9e8c6cdb2a02 (three-pass probe).
MASTER_THREE_PASS_LEDGER: dict[int, list[dict]] = {
    2: [{"agent": "maestro", "state": "pending", "reason": None}],
    3: [{"agent": "web-anthropic", "state": "pending", "reason": None}],
    4: [{"agent": "web-anthropic", "state": "pending", "reason": None}],
    5: [{"agent": "ulg-cortex-symbiosis", "state": "pending", "reason": None}],
    6: [{"agent": "cursor", "state": "pending", "reason": None}],
    7: [{"agent": "maestro", "state": "pending", "reason": None}],
}


def _normalize_ledger_probe(rows: list[dict], turn_id: int) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "agent": r["agent"],
                "turn_id": int(r["turn_id"]),
                "state": r["state"],
                "reason": r.get("reason"),
            }
        )
    assert all(r["turn_id"] == turn_id for r in out)
    return out


@pytest.mark.parametrize("path", [2, 3, 4, 5, 6, 7])
def test_three_pass_ledger_master_parity(
    path: int,
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, _drops = run_three_pass_path_probe(path, monkeypatch)
    assert ledger, f"path {path}: expected pending ledger row after pass 3"
    turn_id = int(ledger[0]["turn_id"])
    normalized = _normalize_ledger_probe(ledger, turn_id)
    master_tpl = MASTER_THREE_PASS_LEDGER[path]
    expected: list[dict] = []
    for row in master_tpl:
        expected.append({**row, "turn_id": turn_id})
    assert normalized == expected


def test_scan_wake_drop_missing_turn_number_does_not_abort_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Malformed since row without turn_number must not stop scan (live API always supplies it)."""
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    turn = {
        "id": 501,
        "thread": "missing-tn",
        "from_agent": "grok-bot-gc1",
        "to_agent": "maestro",
        "subject": "QUESTION|x",
        "read_at": "2020-01-01 00:00:00",
    }

    class Store:
        def __init__(self) -> None:
            self.last: list[dict] = []

        def cursor(self) -> dict:
            return {"high_water_turn_id": 0}

        def since(self, _high: int) -> list[dict]:
            return [turn]

        def scan(self, _high: int, new_hw: int, rows: list[dict]) -> None:
            self.last = list(rows)

        def holder(self, lane: str) -> dict:
            return {"registration_id": "r", "seat_state": "driving"}

        def subscription(self, agent: str, thread_id: str) -> None:
            return (
                "cse",
                {
                    "surface": "cse",
                    "agent_id": thread_id,
                    "id": 1,
                    "state": "subscribed",
                },
            )

    store = Store()
    scan_once(store)  # type: ignore[arg-type]
    assert store.last == []
    with connect() as conn:
        drop = conn.execute(
            "SELECT turn_number, reason FROM endpoint_wake_scan_drops WHERE turn_id=?",
            (501,),
        ).fetchone()
    assert drop is not None
    assert drop["turn_number"] == 0
    assert drop["reason"] == "skipped:already_read"
