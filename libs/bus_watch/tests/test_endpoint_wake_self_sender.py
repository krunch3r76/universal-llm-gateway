"""Scan-time self-wake guard and suppression tombstones (friction:43076)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
                "body, read_at, from_agent_id, created_at FROM turns WHERE id>? "
                "ORDER BY id LIMIT ?",
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

    def recent_deliveries(
        self, thread: str, since_s: int, *, before: str | None = None
    ) -> list[str]:
        from agent_bus_store.db.endpoint_wake_ledger import recent_delivered_agent_ids

        return recent_delivered_agent_ids(thread, since_s, before=before)

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


def test_api_role_intermediate_subjects_not_peer_wake() -> None:
    from bus_watch.endpoint_wake_dispatcher import peer_operator_wake_from

    assert not peer_operator_wake_from("artisan", subject="CHECKPOINT|leg-boundary")
    assert not peer_operator_wake_from("skeptic", subject="progress|batch-2")
    assert peer_operator_wake_from("artisan", subject="DONE|final-reply")


def test_api_generate_peer_wake_from_catalog_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.endpoint_wake_dispatcher import (
        _API_GENERATE_PEER_WAKE_FALLBACK,
        _api_generate_peer_wake_from,
    )

    _api_generate_peer_wake_from.cache_clear()

    def _boom() -> list[str]:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(
        "agent_seat.dispatch_role_catalog.generate_roles",
        _boom,
    )
    assert _api_generate_peer_wake_from() == _API_GENERATE_PEER_WAKE_FALLBACK
    _api_generate_peer_wake_from.cache_clear()


def test_api_role_to_orion_intermediate_not_woken() -> None:
    from bus_watch.endpoint_wake_dispatcher import qualify

    verdict = qualify(
        {
            "from_agent": "synthesizer",
            "to_agent": "orion",
            "subject": "STATUS|not-an-answer",
            "thread": "22388",
        },
        cse=False,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "default_suppress"


def test_api_role_self_echo_drops_before_peer_wake() -> None:
    from bus_watch.endpoint_wake_dispatcher import qualify

    verdict = qualify(
        {
            "from_agent": "artisan",
            "to_agent": "artisan",
            "subject": "DONE|self",
            "thread": "22554",
        },
        cse=False,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "drop_self_or_broadcast"


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


def test_cursor_shared_mailbox_with_from_agent_id_is_tombstoned(db: None) -> None:
    """AC2: ``from_agent_id`` proves self tab even when ``from_agent`` is ``cursor``."""
    thread = _thread("f43033-m1")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|x",
        body="x",
        from_agent_id=TAB,
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]

    rows = _ledger(thread)
    assert len(rows) == 1
    assert rows[0]["state"] == "skipped"
    assert rows[0]["reason"] == "suppressed:drop_self_sender"


def _stamp(seconds_ago: int = 0) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _delivered_row(
    thread: str,
    *,
    turn_id: int,
    turn_number: int,
    finished_at: str,
    tab: str,
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, created_at, "
            "finished_at, delivery_agent_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                "cursor",
                thread,
                turn_id,
                turn_number,
                "delivered",
                finished_at,
                finished_at,
                tab,
            ),
        )


def _cursor_self_post(
    thread: str, *, created_at: str
) -> tuple[int, int]:
    post_id, _ts, post_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-window-arc-coordinator",
        subject="DONE|self",
        body="x",
    )
    with connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at=? WHERE id=?",
            (created_at, post_id),
        )
    return post_id, post_n


def test_recency_fallback_single_tab_suppresses_shared_cursor(db: None) -> None:
    """AC3: one delivered tab in [created_at−N, created_at] suppresses bare ``cursor``."""
    thread = _thread("f43033-m2-one")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    wake_turn, _ts, wake_n, _c = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="DONE|wake",
        body="w",
    )
    post_at = _stamp(0)
    delivery_at = _stamp(30)
    _delivered_row(
        thread,
        turn_id=wake_turn,
        turn_number=wake_n,
        finished_at=delivery_at,
        tab=TAB,
    )
    post_id, _ = _cursor_self_post(thread, created_at=post_at)
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (post_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "skipped"
    assert row["reason"] == "suppressed:drop_self_sender_recency"


def test_recency_two_tabs_in_window_not_suppressed(db: None) -> None:
    """AC3: ambiguous sole-tab signal breaks when two tabs delivered in window."""
    thread = _thread("f43033-m2-two-tabs")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    _subscribe("cursor", thread, PEER_TAB)
    t1, _, n1, _ = insert_turn(
        thread=thread, from_agent="a", to_agent="cursor", subject="DONE|1", body="x"
    )
    t2, _, n2, _ = insert_turn(
        thread=thread, from_agent="b", to_agent="cursor", subject="DONE|2", body="x"
    )
    post_at = _stamp(0)
    _delivered_row(
        thread, turn_id=t1, turn_number=n1, finished_at=_stamp(30), tab=TAB
    )
    _delivered_row(
        thread,
        turn_id=t2,
        turn_number=n2,
        finished_at=_stamp(15),
        tab=PEER_TAB,
    )
    post_id, _ = _cursor_self_post(thread, created_at=post_at)
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (post_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"


def test_recency_delivery_before_window_not_suppressed(db: None) -> None:
    """AC3: sole delivery 400s before ``created_at`` does not suppress."""
    thread = _thread("f43033-m2-stale")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    wake_turn, _, wake_n, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="DONE|wake",
        body="w",
    )
    post_at = _stamp(0)
    _delivered_row(
        thread,
        turn_id=wake_turn,
        turn_number=wake_n,
        finished_at=_stamp(400),
        tab=TAB,
    )
    post_id, _ = _cursor_self_post(thread, created_at=post_at)
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (post_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"


def test_recency_delivery_after_created_at_not_suppressed(db: None) -> None:
    """Finding 1: deliveries after the turn's ``created_at`` must not count."""
    thread = _thread("f43033-m2-future-delivery")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    post_at = _stamp(60)
    post_id, _post_n = _cursor_self_post(thread, created_at=post_at)
    wake_turn, _, wake_n, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="DONE|wake",
        body="w",
    )
    _delivered_row(
        thread,
        turn_id=wake_turn,
        turn_number=wake_n,
        finished_at=_stamp(0),
        tab=TAB,
    )
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (post_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"


def test_recency_disabled_when_env_zero(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: ``ENDPOINT_WAKE_SELF_RECENCY_S=0`` disables M2."""
    monkeypatch.setenv("ENDPOINT_WAKE_SELF_RECENCY_S", "0")
    import importlib

    import bus_watch.endpoint_wake_self_sender as self_sender

    importlib.reload(self_sender)
    thread = _thread("f43033-m2-off")
    _subscribe("grok-bot-window-arc-coordinator", thread, TAB)
    _subscribe("cursor", thread, TAB)
    wake_turn, _, wake_n, _ = insert_turn(
        thread=thread,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="DONE|wake",
        body="w",
    )
    post_at = _stamp(0)
    _delivered_row(
        thread,
        turn_id=wake_turn,
        turn_number=wake_n,
        finished_at=_stamp(5),
        tab=TAB,
    )
    post_id, _ = _cursor_self_post(thread, created_at=post_at)
    _rewind_cursor()

    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE turn_id=?",
            (post_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "pending"
    importlib.reload(self_sender)


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
