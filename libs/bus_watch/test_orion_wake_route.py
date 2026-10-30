"""Offline tests for Orion synthetic wake route and echo drop."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn

from bus_watch.endpoint_wake_dispatcher import (
    _orion_synthetic_route,
    qualify,
    resolve_target,
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
                    "SELECT subject, body, turn_number, from_agent FROM turns "
                    "WHERE thread=? AND turn_number>? ORDER BY turn_number DESC LIMIT ?",
                    (thread, after_turn, last),
                ).fetchall()
            return {
                "turns": [dict(row) for row in reversed(rows)],
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


def test_orion_seat_registration_scan_ignores_quoted_mention(bus_db: None) -> None:
    create_thread(thread_id=_ORION_HOUSE, slug="orion-route-quoted")
    insert_turn(
        thread=_ORION_HOUSE,
        from_agent="orion",
        to_agent="maestro",
        subject="FACT|quoted",
        body='Earlier thread cited "TYPE: SEAT_REGISTRATION" without a stamp.\n',
    )
    insert_turn(
        thread=_ORION_HOUSE,
        from_agent="orion",
        to_agent="maestro",
        subject="SEAT_REGISTRATION|orion-HOP06",
        body=(f"TYPE: SEAT_REGISTRATION\nNew exact URL: {_HOP06_NEW_URL}\n"),
    )

    class Store:
        def turns(self, thread: str, *, after_turn: int = 0, last: int = 100):
            from agent_bus_store.db.connection import connect

            with connect() as conn:
                rows = conn.execute(
                    "SELECT subject, body, turn_number, from_agent FROM turns "
                    "WHERE thread=? AND turn_number>? ORDER BY turn_number DESC LIMIT ?",
                    (thread, after_turn, last),
                ).fetchall()
            return {
                "turns": [dict(row) for row in reversed(rows)],
                "truncated": False,
            }

        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

    bound = _orion_synthetic_route("orion", _ORION_HOUSE, store=Store())
    assert bound is not None
    assert bound[1]["title"] == _HOP06_NEW_URL


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
