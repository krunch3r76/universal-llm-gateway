"""Per-kind endpoint wake debounce: orion_chat 30s, others 600s."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _scan_orion_pending(
    *,
    turn_id: int,
    turn_number: int,
    now: str,
    high_water: int = 0,
) -> None:
    advance_scan(
        "endpoint-wake-dispatcher",
        high_water,
        turn_id,
        [
            {
                "agent": "orion",
                "thread_id": _ORION_HOUSE,
                "turn_id": turn_id,
                "turn_number": turn_number,
            }
        ],
        now=now,
    )


def test_orion_chat_claim_after_delivery_eligible_at_30s_not_29s(db: None) -> None:
    """Break: global 600s debounce delays Orion ~8m after a delivered wake."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-deb")
    a_id, _t, a_n, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|a",
        body="a",
    )
    b_id, _t2, b_n, _c2 = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|b",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    _scan_orion_pending(turn_id=a_id, turn_number=a_n, now="2020-01-01 00:00:00")
    wake = claim_due_wakes(now="2020-01-01 00:00:30")
    assert wake["wake_id"]
    record_wake_result(
        wake["wake_id"], "delivered", "ok", "exact", None, None, now="2020-01-01 00:00:31"
    )
    _scan_orion_pending(
        turn_id=b_id, turn_number=b_n, now="2020-01-01 00:00:40", high_water=a_id
    )
    assert claim_due_wakes(now="2020-01-01 00:01:00")["wake_id"] is None
    later = claim_due_wakes(now="2020-01-01 00:01:01")
    assert later["wake_id"]
    assert [r["turn_id"] for r in later["rows"]] == [b_id]


def test_non_orion_wake_still_debounces_600s_after_delivery(db: None) -> None:
    """Break: lowering Orion debounce must not shrink other endpoint kinds."""
    detail = create_thread(thread_id=None, slug="grok-deb")
    assert detail is not None
    thread = str(detail["id"])
    a_id, _t, a_n, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|a",
        body="a",
    )
    b_id, _t2, b_n, _c2 = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|b",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        a_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": a_id,
                "turn_number": a_n,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    wake = claim_due_wakes(now="2020-01-01 00:00:01")
    record_wake_result(
        wake["wake_id"], "delivered", "ok", "exact", 1, None, now="2020-01-01 00:00:02"
    )
    advance_scan(
        "endpoint-wake-dispatcher",
        a_id,
        b_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": b_id,
                "turn_number": b_n,
            }
        ],
        now="2020-01-01 00:00:30",
    )
    assert claim_due_wakes(now="2020-01-01 00:00:40")["wake_id"] is None
    later = claim_due_wakes(now="2020-01-01 00:10:03")
    assert [r["turn_id"] for r in later["rows"]] == [b_id]


def test_orion_three_turns_within_30s_one_claim(db: None) -> None:
    """Break: per-turn claims without coalesce → three pastes for one burst."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-batch-deb")
    turn_specs: list[tuple[int, int]] = []
    for i, offset_s in enumerate((0, 10, 20)):
        turn_id, _ts, number, _c = insert_turn(
            thread=_ORION_HOUSE,
            from_agent="maestro",
            to_agent="orion",
            subject=f"REQUEST|{i}",
            body="b",
        )
        turn_specs.append((int(turn_id), int(number)))
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    hw = 0
    for (turn_id, number), offset_s in zip(turn_specs, (0, 10, 20), strict=True):
        _scan_orion_pending(
            turn_id=turn_id,
            turn_number=number,
            now=f"2020-01-01 00:00:{offset_s:02d}",
            high_water=hw,
        )
        hw = turn_id
    assert claim_due_wakes(now="2020-01-01 00:00:29")["wake_id"] is None
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    assert claimed["wake_id"]
    claimed_ids = [r["turn_id"] for r in claimed["rows"] if r.get("state") == "claimed"]
    assert claimed_ids == [t[0] for t in turn_specs]
    assert ENDPOINT_WAKE_DEBOUNCE_S_ORION_CHAT == 30
