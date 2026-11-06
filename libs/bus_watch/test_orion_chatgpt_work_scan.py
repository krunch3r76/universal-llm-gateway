"""Real scan_once path for Orion chatgpt_work coordinator wakes."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import insert_chatgpt_work_row_for_test
from agent_bus_store.db.endpoint_wake_ledger import advance_scan, get_cursor
from bus_watch.endpoint_wake_dispatcher import qualify, scan_once
from bus_watch.endpoint_wake_transport import classify_orion

pytestmark = pytest.mark.offline

_URL = (
    "https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion/c/"
    "6aecb6b3-4a8c-83e8-8d72-cf08d91e5f7e"
)
_CONV = "6aecb6b3-4a8c-83e8-8d72-cf08d91e5f7e"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_classify_orion_non_empty_composer_defers(db: None) -> None:
    state, reason = classify_orion({"ok": False, "reason": "non_empty_composer"})
    assert state == "pending"
    assert reason == "non_empty_composer"


def test_qualify_native_api_on_behalf_execution_reply(db: None) -> None:
    turn = {
        "from_agent": "synthesizer",
        "to_agent": "orion",
        "thread": "22388",
        "subject": "synthesizer reply — execution 31cc6836",
    }
    verdict = qualify(turn, cse=False, store=None)
    assert verdict.wake is True
    assert verdict.rule_id == "wake_on_behalf_execution_reply"


def test_scan_once_enqueues_closeout_with_chatgpt_work_binding(db: None) -> None:
    detail = create_thread(thread_id=None, slug="orion-coord-scan")
    assert detail is not None
    thread = str(detail["id"])
    insert_chatgpt_work_row_for_test(
        instance_id=f"orion-{thread}",
        home_house_id=thread,
        conversation_url=_URL,
    )
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="orion",
        subject="CLOSEOUT|ORION-22388-BOUND-20261106-A",
        body="probe",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    captured: list[dict] = []

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
            captured.extend(rows)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            return None

        def holder(self, lane: str) -> dict | None:
            return None

        def lane_parent(self, lane: str) -> str | None:
            return None

    scan_once(Store())  # type: ignore[arg-type]
    assert len(captured) == 1
    row = captured[0]
    assert row["agent"] == "orion"
    assert row["thread_id"] == thread
    assert row["turn_id"] == turn_id
    assert row["turn_number"] == number
    assert row["decided_by_rule"] == "wake_gate_or_directive"
    assert row.get("display") == "12"
    assert row.get("delivery_agent_id") == _CONV
