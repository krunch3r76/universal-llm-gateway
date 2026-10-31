"""Dispatcher chatgpt_work route uses explicit conversation URL."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import insert_chatgpt_work_row_for_test
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    get_cursor,
    resolve_orion_chat_wake,
)
from bus_watch.endpoint_wake_dispatcher import (
    _chatgpt_work_route,
    resolve_target,
    scan_once,
)

pytestmark = pytest.mark.offline

_HOME = "910001"
_INST = "inst-a"
_URL = "https://chatgpt.com/g/g-p-proj-a/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_work_route_uses_explicit_url_not_glass(db: None) -> None:
    create_thread(thread_id=_HOME, slug="work-route")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    with patch("cursor_bridge.wake_delivery.normalize_target") as norm:
        route = _chatgpt_work_route("orion", _HOME)
        norm.assert_not_called()
    assert route is not None
    _, sub = route
    assert sub["title"] == _URL


def test_maestro_recipient_does_not_self_route_instance(db: None) -> None:
    """Terminal maestro mail on a satellite house must not bind chatgpt_work."""
    create_thread(thread_id=_HOME, slug="self-origin")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    assert _chatgpt_work_route("maestro", _HOME) is None
    assert resolve_orion_chat_wake(_HOME, agent="maestro")["state"] == "unbound"

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

    assert resolve_target(Store(), "maestro", _HOME) is None  # type: ignore[arg-type]
    assert _chatgpt_work_route("orion", _HOME) is not None


def test_scan_once_maestro_answer_skips_instance_chatgpt_route(db: None) -> None:
    """Break: maestro ANSWER on a satellite house enqueues chatgpt_work self-wake."""
    create_thread(thread_id=_HOME, slug="21342-loop")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST,
        home_house_id=_HOME,
        conversation_url=_URL,
    )
    t_id, _ts, t_n, _c = insert_turn(
        thread=_HOME,
        from_agent="orion",
        to_agent="maestro",
        subject="ANSWER|terminal|no-reACK",
        body="done",
    )
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

        def scan(self, expected: int, new_hw: int, pending: list[dict]) -> dict:
            captured.extend(pending)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                pending,
                now="2020-01-01 00:00:01",
            )

        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

        def holder(self, lane: str):
            return None

    scan_once(Store())  # type: ignore[arg-type]
    assert captured == []
