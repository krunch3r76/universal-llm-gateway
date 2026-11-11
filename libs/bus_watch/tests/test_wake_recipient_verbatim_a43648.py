"""Verbatim bus specimens for a:43648 (threads 23056, 23059, 22796)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.db.endpoint_wake_ledger import CONSUMER, advance_scan, get_cursor
from bus_watch.endpoint_wake_dispatcher import (
    _COORD_WAKE_EXCLUDED_TO,
    _WAKE_COORD_SUBSCRIBER_RULE,
    STAGE_MANAGER_ADDRESSES,
    qualify,
    scan_once,
)

# 23056 id 201113 / turn 5 — COORD that failed to wake slb-owner (summarizer-5).
SPECIMEN_23056_COORD = {
    "from_agent": "s4-bus-addressing-build",
    "to_agent": "slb-owner",
    "subject": (
        "COORD | bus-addressing decision 7: instance_id on seat rows + "
        "mailbox lane at birth; 7 unread requests on 22956"
    ),
}
# 23056 id 201147 / turn 7 — REQUEST re-send that delivered.
SPECIMEN_23056_REQUEST = {
    "from_agent": "s5-owner-hygiene",
    "to_agent": "slb-owner",
    "subject": "REQUEST | read turns 5 and 6 on 23056 now (wake row 6969 bound)",
}
# 23059 turn 2 id 201146 — POINTER suppressed.
SPECIMEN_23059_POINTER = {
    "from_agent": "s5-owner-hygiene",
    "to_agent": "tlr-owner",
    "subject": (
        "POINTER: fe5564d5 drain-refusal review already dispatched "
        "(exec 2e75f32f); add row h, don't duplicate"
    ),
}
# 23059 turn 3 id 201148 — REQUEST re-send.
SPECIMEN_23059_REQUEST = {
    "from_agent": "s5-owner-hygiene",
    "to_agent": "tlr-owner",
    "subject": (
        "REQUEST | read turn 2: fe5564d5 review is exec 2e75f32f on 23070, add as row h"
    ),
}


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def _rewind(conn) -> None:
    conn.execute(
        "UPDATE endpoint_wake_cursor SET high_water_turn_id=0 WHERE consumer=?",
        (CONSUMER,),
    )


class _Store:
    def cursor(self):
        return get_cursor()

    def since(self, after_id: int, limit: int = 50):
        with connect() as c:
            rows = c.execute(
                "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def scan(self, expected: int, new_hw: int, rows: list):
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

    def holder(self, lane: str):
        reg = str(lane or "").strip()
        if reg:
            return {
                "registration_id": reg,
                "chat_url": f"https://claude.ai/cowork/{reg}",
                "seat_state": "houseless",
            }
        return None

    def lane_parent(self, lane: str):
        return None


def _seed_sub(agent: str, thread: str) -> None:
    create_thread(thread_id=thread, slug=f"verb-{thread}")
    subscribe_endpoint(
        agent=agent,
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="sub",
        agent_id=f"reg-{agent}",
        bound_by="verbatim-a43648",
    )


def _scan_turn(thread: str, *, from_agent: str, to_agent: str, subject: str) -> None:
    create_thread(thread_id=thread, slug=f"scan-{thread}")
    insert_turn(
        thread=thread,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        body="x",
    )
    get_cursor()
    with connect() as conn:
        _rewind(conn)
    scan_once(_Store())  # type: ignore[arg-type]


def _pending(thread: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM endpoint_wake_ledger WHERE thread_id=? AND state='pending'",
            (thread,),
        ).fetchall()
    return [dict(r) for r in rows]


def _ledger_rows(thread: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT state, reason, decided_by_rule FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchall()
    return [dict(r) for r in rows]


def test_verbatim_coord_23056_pending_wake(db: None) -> None:
    _seed_sub("slb-owner", "23056")
    _scan_turn("23056", **SPECIMEN_23056_COORD)
    wakes = _pending("23056")
    assert len(wakes) == 1
    assert wakes[0]["agent"] == "slb-owner"
    assert wakes[0]["decided_by_rule"] == _WAKE_COORD_SUBSCRIBER_RULE


def test_verbatim_pointer_23059_suppressed_tombstone(db: None) -> None:
    _seed_sub("tlr-owner", "23059")
    _scan_turn("23059", **SPECIMEN_23059_POINTER)
    assert _pending("23059") == []
    rows = _ledger_rows("23059")
    assert len(rows) == 1
    assert rows[0]["state"] == "skipped"
    assert "default_suppress" in str(rows[0]["reason"])


def test_verbatim_request_23059_pending_wake(db: None) -> None:
    _seed_sub("tlr-owner", "23059")
    _scan_turn("23059", **SPECIMEN_23059_REQUEST)
    assert len(_pending("23059")) == 1


def test_verbatim_request_23056_pending_wake(db: None) -> None:
    _seed_sub("slb-owner", "23056")
    _scan_turn("23056", **SPECIMEN_23056_REQUEST)
    assert len(_pending("23056")) == 1


def test_coord_self_address_does_not_wake() -> None:
    verdict = qualify(
        {
            "to_agent": "slb-owner",
            "from_agent": "slb-owner",
            "subject": SPECIMEN_23056_COORD["subject"],
            "thread": "23056",
        },
        cse=True,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "drop_self_or_broadcast"


@pytest.mark.parametrize(
    "to_agent",
    sorted(_COORD_WAKE_EXCLUDED_TO | STAGE_MANAGER_ADDRESSES),
)
def test_coord_to_excluded_agents_never_uses_coord_rule(to_agent: str) -> None:
    class _SubStore(_Store):
        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            return ("exact", {"state": "subscribed", "thread_scope": thread_id})

    verdict = qualify(
        {
            "to_agent": to_agent,
            "from_agent": "dispatch",
            "subject": SPECIMEN_23056_COORD["subject"],
            "thread": "23056",
        },
        cse=True,
        store=_SubStore(),  # type: ignore[arg-type]
    )
    assert verdict.rule_id != _WAKE_COORD_SUBSCRIBER_RULE


def test_coord_ping_pong_cooldown_second_turn_suppressed(db: None) -> None:
    thread = "99902"
    _seed_sub("agent-a", "99901")
    _seed_sub("agent-b", thread)
    turn_id, *_ = insert_turn(
        thread=thread,
        from_agent="agent-a",
        to_agent="agent-b",
        subject="COORD | prior",
        body="x",
    )
    with write_connect() as conn:
        conn.execute(
            """
            INSERT INTO endpoint_wake_ledger (
                agent, thread_id, turn_id, turn_number, state, reason,
                decided_by_rule, created_at
            ) VALUES (?, ?, ?, 1, 'pending', '', 'wake_coord_subscriber',
                      datetime('now'))
            """,
            ("agent-b", thread, turn_id),
        )
    verdict = qualify(
        {
            "to_agent": "agent-b",
            "from_agent": "agent-a",
            "subject": "COORD | follow-up",
            "thread": "99902",
        },
        cse=True,
        store=_Store(),  # type: ignore[arg-type]
    )
    assert verdict.wake is False
    assert verdict.rule_id == "default_suppress"


def test_coord_rule_after_self_drop_before_default_suppress(db: None) -> None:
    """drop_self_or_broadcast (rule 0) beats COORD; subscribed COORD beats default."""
    assert (
        qualify(
            {
                "to_agent": "x",
                "from_agent": "x",
                "subject": "COORD | x",
                "thread": "t",
            },
            cse=False,
            store=None,
        ).rule_id
        == "drop_self_or_broadcast"
    )
    _seed_sub("slb-owner", "23056")
    assert (
        qualify(
            {
                "to_agent": "slb-owner",
                "from_agent": "dispatch",
                "subject": SPECIMEN_23056_COORD["subject"],
                "thread": "23056",
            },
            cse=True,
            store=_Store(),  # type: ignore[arg-type]
        ).rule_id
        == _WAKE_COORD_SUBSCRIBER_RULE
    )


def test_one_tombstone_per_suppressed_turn(db: None) -> None:
    _seed_sub("tlr-owner", "23059")
    _scan_turn("23059", **SPECIMEN_23059_POINTER)
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM endpoint_wake_ledger WHERE thread_id='23059'"
        ).fetchone()[0]
    assert int(n) == 1
