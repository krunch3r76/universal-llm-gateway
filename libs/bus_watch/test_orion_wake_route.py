"""Offline tests for Orion synthetic wake route and echo drop."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import insert_chatgpt_work_row_for_test
from agent_bus_store.db.endpoint_wake_ledger import advance_scan, get_cursor

from bus_watch.endpoint_wake_dispatcher import (
    _chatgpt_work_route,
    _orion_synthetic_route,
    qualify,
    resolve_target,
    scan_once,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"
_HOP06_NEW_URL = (
    "https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
    "/c/6ae4cfde-151c-83e8-8966-fdd8893f472d"
)
_HOP06_CONV_ID = "6ae4cfde-151c-83e8-8966-fdd8893f472d"


def test_orion_synthetic_route_house_only() -> None:
    bound = _orion_synthetic_route("orion", "21207")
    assert bound is not None
    assert bound[1]["surface"] == "chatgpt"
    assert _orion_synthetic_route("orion", "99999") is None
    assert _orion_synthetic_route("cursor", "21207") is None


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()


def test_orion_synthetic_route_seat_registration_hop06(bus_db: None) -> None:
    create_thread(thread_id=_ORION_HOUSE, slug="orion-route-hop06")
    insert_turn(
        thread=_ORION_HOUSE,
        from_agent="orion",
        to_agent="maestro",
        subject="SEAT_REGISTRATION|orion-HOP06",
        body=(
            "TYPE: SEAT_REGISTRATION\n"
            "Old exact URL: https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
            "/c/6ae44d27-06cc-83e8-8b8a-df9b819755bd\n"
            f"New exact URL: {_HOP06_NEW_URL}\n"
        ),
    )

    class Store:
        def turns(self, thread: str, *, after_turn: int = 0, last: int = 100):
            from agent_bus_store.db.connection import connect

            with connect() as conn:
                rows = conn.execute(
                    "SELECT subject, body, turn_number FROM turns "
                    "WHERE thread=? AND turn_number>? ORDER BY turn_number ASC LIMIT ?",
                    (thread, after_turn, last),
                ).fetchall()
            return {
                "turns": [dict(row) for row in rows],
                "truncated": False,
            }

        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

    store = Store()
    bound = _orion_synthetic_route("orion", _ORION_HOUSE, store=store)
    assert bound is not None
    assert bound[1]["agent_id"] == _HOP06_CONV_ID
    assert bound[1]["title"] == _HOP06_NEW_URL

    route, row = resolve_target(store, "orion", _ORION_HOUSE)  # type: ignore[arg-type]
    assert route == "exact"
    assert row["agent_id"] == _HOP06_CONV_ID


_SAT_HOME = "910001"
_SAT_INST = "inst-hop06-self-origin"
_SAT_URL = "https://chatgpt.com/g/g-p-proj-a/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_hop06_route_delivered_while_satellite_self_origin_suppressed(
    bus_db: None,
) -> None:
    """Hop06 seat registration still routes; maestro terminal ANSWER must not self-wake."""
    create_thread(thread_id=_ORION_HOUSE, slug="hop06-plus-21342")
    insert_turn(
        thread=_ORION_HOUSE,
        from_agent="orion",
        to_agent="maestro",
        subject="SEAT_REGISTRATION|orion-HOP06",
        body=(
            "TYPE: SEAT_REGISTRATION\n"
            "Old exact URL: https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion"
            "/c/6ae44d27-06cc-83e8-8b8a-df9b819755bd\n"
            f"New exact URL: {_HOP06_NEW_URL}\n"
        ),
    )
    assert _orion_synthetic_route("orion", _ORION_HOUSE) is not None
    assert (
        _orion_synthetic_route("orion", _ORION_HOUSE)[1]["agent_id"] == _HOP06_CONV_ID
    )

    create_thread(thread_id=_SAT_HOME, slug="21342-loop")
    insert_chatgpt_work_row_for_test(
        instance_id=_SAT_INST,
        home_house_id=_SAT_HOME,
        conversation_url=_SAT_URL,
    )
    assert _chatgpt_work_route("maestro", _SAT_HOME) is None
    assert _chatgpt_work_route("orion", _SAT_HOME) is not None

    insert_turn(
        thread=_SAT_HOME,
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


def test_qualify_drops_orion_echo() -> None:
    turn = {
        "to_agent": "orion",
        "from_agent": "orion",
        "subject": "ANSWER|self",
        "thread": "21207",
        "id": 1,
    }
    verdict = qualify(turn, cse=False, store=None)
    assert verdict.wake is False
    assert verdict.rule_id in {"drop_self_or_broadcast", "drop_orion_echo"}
