"""COORD wake + suppress ledger visibility (summarizer-5)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.db.endpoint_wake_ledger import CONSUMER, advance_scan, get_cursor
from agent_bus_store.testing.wake_cursor_test_support import rewind_wake_cursor_for_test
from bus_watch.endpoint_wake_dispatcher import qualify, scan_once

_SUBSCRIBER = "slb-owner"
_THREAD_23056 = "23056"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


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
        return None

    def lane_parent(self, lane: str):
        return None


def test_coord_head_wakes_subscribed_non_alias(db: None) -> None:
    create_thread(thread_id=_THREAD_23056, slug="coord-wake")
    subscribe_endpoint(
        agent=_SUBSCRIBER,
        thread_scope=_THREAD_23056,
        surface="cse",
        display="cse",
        title="slb",
        agent_id="reg-slb",
        bound_by="test",
    )
    verdict = qualify(
        {
            "to_agent": _SUBSCRIBER,
            "from_agent": "dispatch",
            "subject": "COORD | pointer",
            "thread": _THREAD_23056,
        },
        cse=True,
        store=_Store(),  # type: ignore[arg-type]
    )
    assert verdict.wake is True
    assert verdict.rule_id == "wake_coord_subscriber"


def test_pointer_default_suppress_leaves_skipped_tombstone(db: None) -> None:
    create_thread(thread_id="23059", slug="pointer-suppress")
    subscribe_endpoint(
        agent=_SUBSCRIBER,
        thread_scope="23059",
        surface="cse",
        display="cse",
        title="slb",
        agent_id="reg-slb-23059",
        bound_by="test",
    )
    insert_turn(
        thread="23059",
        from_agent="dispatch",
        to_agent=_SUBSCRIBER,
        subject="POINTER | progress note",
        body="x",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    scan_once(_Store())  # type: ignore[arg-type]
    with connect() as conn:
        rows = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE thread_id='23059'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["state"] == "skipped"
    assert "drop_dispatch_intermediate" in str(rows[0]["reason"])
