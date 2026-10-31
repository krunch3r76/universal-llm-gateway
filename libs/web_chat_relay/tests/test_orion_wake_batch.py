"""Batch turn parsing, pointer shape, and ledger receipts for Orion wakes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.orion_wake_owner_adapter import shared_wake_delivery_owner
from bus_watch.endpoint_wake_transport import classify_orion
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_URL,
    build_wake_pointer,
    deliver_orion_chat_wake,
    format_turn_pointer_label,
    parse_turn_numbers,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_parse_turn_numbers_accepts_batch_and_list() -> None:
    assert parse_turn_numbers("69,70,71,72,73") == [69, 70, 71, 72, 73]
    assert parse_turn_numbers([69, 70, 71]) == [69, 70, 71]
    assert parse_turn_numbers(74) == [74]
    assert parse_turn_numbers("74") == [74]


def test_parse_turn_numbers_bad_input() -> None:
    assert parse_turn_numbers("69,xx,71") is None
    assert parse_turn_numbers("") is None


def test_format_turn_pointer_label_contiguous_range() -> None:
    assert format_turn_pointer_label([69, 70, 71, 72, 73]) == "69–73"


def test_build_wake_pointer_batch_wording() -> None:
    line = build_wake_pointer(thread=_ORION_HOUSE, turn="69–73", from_agent="maestro")
    assert f"thread {_ORION_HOUSE} turns 69–73" in line
    assert "read turns 69–73 only" in line
    assert "to=maestro" in line


def test_build_wake_pointer_reply_to_waking_sender_not_hardcoded_maestro() -> None:
    line = build_wake_pointer(thread=_ORION_HOUSE, turn=321, from_agent="cursor")
    assert "from cursor" in line
    assert "to=cursor" in line
    assert "to=maestro" not in line


def test_classify_orion_pipeline_error_and_bad_turn() -> None:
    assert classify_orion(None, http_status=500) == ("pending", "pipeline_error:500")
    assert classify_orion("html", http_status=502) == ("pending", "pipeline_error:502")
    assert classify_orion({"ok": False, "reason": "bad_turn"}) == (
        "refused",
        "bad_turn",
    )


@pytest.mark.asyncio
async def test_batch_comma_turn_one_paste_five_receipts(db: None) -> None:
    """Break: int(comma_turn) in handler → 500; expect one paste + five delivered rows."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-batch")
    turn_ids: list[int] = []
    turn_numbers = list(range(69, 74))
    for n in turn_numbers:
        turn_id, _ts, _number, _c = insert_turn(
            thread=_ORION_HOUSE,
            from_agent="maestro",
            to_agent="orion",
            subject=f"REQUEST|{n}",
            body="b",
        )
        turn_ids.append(int(turn_id))
    endpoint = PINNED_CONVERSATION_URL.split("?", 1)[0]
    paste_calls: list[str] = []

    async def _fake_deliver(*, turn: int | str, **kwargs: object) -> dict:
        paste_calls.append(str(turn))
        return {
            "ok": True,
            "reason": "delivered",
            "pasted": True,
            "delivered": True,
            "pointer_len": 10,
        }

    opts = {
        "thread": _ORION_HOUSE,
        "turn": "69,70,71,72,73",
        "from_agent": "maestro",
        "conversation_url": endpoint,
        "wake_id": "wake-batch-69-73",
        "turn_ids": turn_ids,
        "turn_numbers": turn_numbers,
        "agent": "orion",
    }
    owner = shared_wake_delivery_owner()
    with patch(
        "web_chat_relay.orion_wake.deliver_orion_wake",
        new=AsyncMock(side_effect=_fake_deliver),
    ):
        out = await deliver_orion_chat_wake(
            thread=_ORION_HOUSE,
            turn="69,70,71,72,73",
            from_agent="maestro",
            conversation_url=endpoint,
            pipeline_options=opts,
            delivery_owner=owner,
        )
    assert out.get("delivered") is True, out
    assert len(paste_calls) == 1
    assert paste_calls[0] == "69–73"
    with connect() as conn:
        rows = conn.execute(
            "SELECT turn_id, state FROM endpoint_wake_ledger WHERE wake_id=? ORDER BY turn_id",
            ("wake-batch-69-73",),
        ).fetchall()
    assert len(rows) == 5
    assert all(str(r["state"]) == "delivered" for r in rows)


@pytest.mark.asyncio
async def test_list_turn_input_delivers(db: None) -> None:
    create_thread(thread_id=_ORION_HOUSE, slug="orion-list")
    ids: list[int] = []
    numbers: list[int] = []
    for n in (10, 11):
        turn_id, _ts, number, _c = insert_turn(
            thread=_ORION_HOUSE,
            from_agent="maestro",
            to_agent="orion",
            subject="REQUEST|x",
            body="b",
        )
        ids.append(int(turn_id))
        numbers.append(int(number))
    endpoint = PINNED_CONVERSATION_URL.split("?", 1)[0]
    opts = {
        "thread": _ORION_HOUSE,
        "turn": numbers,
        "from_agent": "maestro",
        "conversation_url": endpoint,
        "wake_id": "wake-list",
        "turn_ids": ids,
        "turn_numbers": numbers,
        "agent": "orion",
    }
    with patch(
        "web_chat_relay.orion_wake.deliver_orion_wake",
        new=AsyncMock(
            return_value={
                "ok": True,
                "delivered": True,
                "pasted": True,
                "reason": "delivered",
            }
        ),
    ):
        out = await deliver_orion_chat_wake(
            thread=_ORION_HOUSE,
            turn=numbers,
            from_agent="maestro",
            pipeline_options=opts,
        )
    assert out.get("delivered") is True
