"""Scan-time self-wake guard and suppression tombstones (friction:43076)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import (
    current_subscription,
    list_subscriptions,
    subscribe_endpoint,
)
from agent_bus_store.db.endpoint_wake_ledger import advance_scan, get_cursor
from bus_watch.endpoint_wake_dispatcher import scan_once

TAB = "21060be7-f0a6-4fa1-9058-b117ce4fc526"
PEER_TAB = "8d2ae5f1-da48-4844-80f9-c69399755fbd"

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


class _Store:
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
        return advance_scan("endpoint-wake-dispatcher", expected, new_hw, rows)

    def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
        row = current_subscription(agent, thread_id)
        if row and row.get("state") == "subscribed":
            return ("exact", row)
        return None

    def subscriptions(self, agent: str = "cursor") -> list[dict]:
        return [
            r for r in list_subscriptions(agent=agent) if r.get("state") == "subscribed"
        ]

    def holder(self, lane: str) -> dict | None:
        return None

    def lane_parent(self, lane: str) -> str | None:
        return None


def _thread(slug: str) -> str:
    detail = create_thread(thread_id=None, slug=slug)
    assert detail is not None
    return str(detail["id"])


def _subscribe(agent: str, thread: str, agent_id: str) -> None:
    subscribe_endpoint(
        agent=agent,
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} test",
        agent_id=agent_id,
        bound_by="test",
    )


def _rewind_cursor() -> None:
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")


def _ledger(thread: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT agent, turn_number, state, reason, decided_by_rule, finished_at "
            "FROM endpoint_wake_ledger WHERE thread_id=? ORDER BY id",
            (thread,),
        ).fetchall()
    return [dict(r) for r in rows]


def test_sender_seat_on_same_tab_is_tombstoned_not_enqueued(db: None) -> None:
    """Specimen 22480#103: coordinator-08 addressed a mailbox its own tab holds."""
    thread = _thread("f43076-self-seat")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor-glass-opus-coordinator-08", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent="cursor-glass-opus-coordinator-08",
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|W4 spec folded",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert len(rows) == 1
    assert rows[0]["state"] == "skipped"
    assert rows[0]["reason"] == "suppressed:drop_self_sender"
    assert rows[0]["decided_by_rule"] == "drop_self_sender"
    assert rows[0]["finished_at"]


def test_sender_tab_uuid_address_is_tombstoned(db: None) -> None:
    thread = _thread("f43076-self-uuid")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent=TAB,
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|x",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert [(r["state"], r["reason"]) for r in rows] == [
        ("skipped", "suppressed:drop_self_sender")
    ]


def test_peer_tab_sender_still_enqueues_pending(db: None) -> None:
    thread = _thread("f43076-peer")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor-glass-argon-coordinator-05", thread, PEER_TAB)
    insert_turn(
        thread=thread,
        from_agent="cursor-glass-argon-coordinator-05",
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|x",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert [(r["state"], r["reason"]) for r in rows] == [("pending", None)]


def test_bare_cursor_sender_is_not_resolved_to_a_tab(db: None) -> None:
    """``cursor`` is a shared mailbox name; it never proves the sender's tab."""
    thread = _thread("f43076-bare-cursor")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|x",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    assert [r["state"] for r in _ledger(thread)] == ["pending"]


@pytest.mark.parametrize("from_agent", ["skeptic", "synthesizer", "artisan", "reviewer"])
def test_api_role_reply_wakes_subscribed_recipient(db: None, from_agent: str) -> None:
    """Friction 43079: API generate roles enqueue wakes like peer operators (22471#91/#92)."""
    thread = _thread(f"f43079-{from_agent}")
    _subscribe("cursor-glass-opus-coordinator-08", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent=from_agent,
        to_agent="cursor-glass-opus-coordinator-08",
        subject="W4 fold-path spec: amendments folded (DONE)",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert [r["state"] for r in rows] == ["pending"]


def test_peer_wake_from_non_glass_surfaces_unchanged() -> None:
    from bus_watch.endpoint_wake_dispatcher import peer_operator_wake_from

    assert peer_operator_wake_from("grok-bot-gc1", subject="MEMO|x")
    assert peer_operator_wake_from("web-anthropic", subject="MEMO|x")
    assert not peer_operator_wake_from("cursor-sdk", subject="CLOSEOUT|x")
    assert not peer_operator_wake_from("conductor-hop", subject="DONE|x")
    assert not peer_operator_wake_from("orion", subject="STATUS|x")
    assert peer_operator_wake_from("orion", subject="ANSWER|x")


def test_routine_dispatch_chatter_still_default_suppress(db: None) -> None:
    thread = _thread("f43079-routine")
    _subscribe("cursor-glass-opus-coordinator-08", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent="conductor-hop",
        to_agent="cursor-glass-opus-coordinator-08",
        subject="dispatch status",
        body="x",
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert [(r["state"], r["reason"], r["decided_by_rule"]) for r in rows] == [
        ("skipped", "suppressed:default_suppress", "default_suppress")
    ]


def test_advance_scan_rejects_unknown_item_state(db: None) -> None:
    thread = _thread("f43076-bad-state")
    turn_id, _created_at, turn_number, _claimed = insert_turn(
        thread=thread, from_agent="a", to_agent="b", subject="DONE|x", body="x"
    )
    hw = int(get_cursor()["high_water_turn_id"])
    with pytest.raises(ValueError):
        advance_scan(
            "endpoint-wake-dispatcher",
            hw,
            hw,
            [
                {
                    "agent": "b",
                    "thread_id": thread,
                    "turn_id": turn_id,
                    "turn_number": turn_number,
                    "state": "delivered",
                }
            ],
        )
