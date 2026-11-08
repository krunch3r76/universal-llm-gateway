"""Cursor must advance through suppressed turns; Work journal wakes stay eligible."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_subscriptions import (
    insert_chatgpt_work_row_for_test,
    list_subscriptions,
)
from agent_bus_store.db.endpoint_wake_ledger import advance_scan, get_cursor
from bus_watch.endpoint_wake_dispatcher import qualify, scan_once

pytestmark = pytest.mark.offline

_HOME = "91022388"
_INST = "orion-91022388"
_URL = (
    "https://chatgpt.com/g/g-p-proj-a/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
)


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _store_factory(*, subscription=None):
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
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                pending,
                now="2020-01-01 00:00:01",
            )

        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return subscription

        def holder(self, lane: str):
            return None

    return Store


def test_suppressed_backlog_advances_cursor_without_pending(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break: all-suppressed batches return early and never CAS the high water."""
    create_thread(thread_id=_HOME, slug="stall-house")
    for i in range(55):
        insert_turn(
            thread=_HOME,
            from_agent="grok-bot-bl1",
            to_agent="grok-bot-gc1",
            subject=f"ACK|batch-{i}",
            body="x",
        )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    mono = {"t": 1000.0}
    import bus_watch.endpoint_wake_dispatcher as ew

    monkeypatch.setattr(ew.time, "monotonic", lambda: mono["t"])
    store_cls = _store_factory(subscription=None)
    scan_once(store_cls())  # type: ignore[arg-type]
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) >= 55


def test_callback_test_stays_suppressed(db: None) -> None:
    create_thread(thread_id=_HOME, slug="cb-test")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    t_id, _, _, _ = insert_turn(
        thread=_HOME,
        from_agent="cursor-sdk",
        to_agent="orion",
        subject="CALLBACK_TEST|probe",
        body="probe",
    )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    verdict = qualify(
        {
            "to_agent": "orion",
            "from_agent": "cursor-sdk",
            "subject": "CALLBACK_TEST|probe",
        },
        cse=False,
        store=None,
    )
    assert verdict.wake is False
    assert verdict.rule_id == "default_suppress"
    store_cls = _store_factory(subscription=None)
    scan_once(store_cls())  # type: ignore[arg-type]
    with connect() as conn:
        rows = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchall()
    assert [tuple(r) for r in rows] == [("skipped", "suppressed:default_suppress")]


def test_closeout_to_orion_enqueues_pending_with_work_route(db: None) -> None:
    create_thread(thread_id=_HOME, slug="work-wake")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    t_id, _, t_n, _ = insert_turn(
        thread=_HOME,
        from_agent="cursor-sdk",
        to_agent="orion",
        subject="DONE|callback-probe",
        body="deliver",
    )
    get_cursor()
    with write_connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    captured: list[dict] = []

    class Store(_store_factory(subscription=None)):
        def scan(self, expected: int, new_hw: int, pending: list[dict]) -> dict:
            captured.extend(pending)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                pending,
                now="2020-01-01 00:00:02",
            )

    scan_once(Store())  # type: ignore[arg-type]
    assert len(captured) == 1
    assert captured[0]["turn_id"] == t_id
    assert captured[0]["turn_number"] == t_n
    with connect() as conn:
        ledger = conn.execute(
            "SELECT state, agent, thread_id FROM endpoint_wake_ledger WHERE turn_id=?",
            (t_id,),
        ).fetchone()
    assert ledger is not None
    assert ledger["state"] == "pending"
    assert ledger["agent"] == "orion"
    assert ledger["thread_id"] == _HOME


def test_list_subscriptions_excludes_work_journal(db: None) -> None:
    create_thread(thread_id=_HOME, slug="proj")
    row = insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    assert list_subscriptions(agent=_INST, thread_scope=_INST) == []
    assert list_subscriptions(agent="orion", thread_scope=_HOME) == []
    assert int(row["id"]) > 0
