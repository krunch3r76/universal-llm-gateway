"""Orion deferred_streaming: ledger retry until idle, coalesce, exhaustion NOTICE."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    ORION_DEFERRED_STREAMING_MAX_ATTEMPTS,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_orion_deferred_streaming,
    record_wake_result,
)

from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _orion_sub() -> dict:
    return {
        "surface": "chatgpt",
        "title": "https://chatgpt.com/c/6ae3dd81-bd34-83e8-b5da-4ddc824685b7",
        "agent_id": "6ae3dd81-bd34-83e8-b5da-4ddc824685b7",
        "id": 1,
        "state": "subscribed",
    }


def _scan_orion_turn(*, turn_id: int, turn_number: int, hw: int, now: str) -> None:
    advance_scan(
        "endpoint-wake-dispatcher",
        hw,
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


def test_deferred_streaming_redelivers_after_retry_at(db: None) -> None:
    """Streaming deferral schedules pending retry; a later claim re-fires delivery."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-retry")
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    _scan_orion_turn(turn_id=turn_id, turn_number=number, hw=0, now="2020-01-01 00:00:00")
    first = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(first["wake_id"])
    sched = record_orion_deferred_streaming(
        wake_id, "exact", 1, None, now="2020-01-01 00:00:31"
    )
    assert not sched["exhausted"]
    assert sched["retry_at"] == "2020-01-01 00:01:01"

    assert claim_due_wakes(now="2020-01-01 00:01:00")["wake_id"] is None
    second = claim_due_wakes(now="2020-01-01 00:01:01")
    assert second["wake_id"]
    assert [r["turn_id"] for r in second["rows"] if r["state"] == "claimed"] == [
        turn_id
    ]

    class Store:
        def claim(self) -> dict:
            return second

        def subscription(self, agent: str, thread_id: str, *, raw_to=None):
            if agent == "orion":
                return ("exact", _orion_sub())
            return None

        def result(self, **payload: object) -> int:
            return record_wake_result(
                str(payload["wake_id"]),
                str(payload["outcome"]),
                str(payload["reason"]) if payload["reason"] is not None else None,
                str(payload["route"]) if payload["route"] is not None else None,
                payload["subscription_id"],  # type: ignore[arg-type]
                payload["cse_registration_id"],  # type: ignore[arg-type]
            )

    with patch(
        "bus_watch.endpoint_wake_dispatcher._orion_post",
        return_value=("delivered", "delivered"),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]

    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row["state"] == "delivered"


def test_three_wakes_during_stream_one_claim_batch(db: None) -> None:
    """Pending coalesce: three turns inside one stream window → one claim group."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-batch")
    specs: list[tuple[int, int]] = []
    for i in range(3):
        tid, _ts, num, _c = insert_turn(
            thread=_ORION_HOUSE,
            from_agent="maestro",
            to_agent="orion",
            subject=f"REQUEST|{i}",
            body="b",
        )
        specs.append((int(tid), int(num)))
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    hw = 0
    for offset, (tid, num) in zip((0, 5, 10), specs, strict=True):
        _scan_orion_turn(
            turn_id=tid, turn_number=num, hw=hw, now=f"2020-01-01 00:00:{offset:02d}"
        )
        hw = tid
    first = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(first["wake_id"])
    record_orion_deferred_streaming(wake_id, "exact", 1, None, now="2020-01-01 00:00:31")
    second = claim_due_wakes(now="2020-01-01 00:01:01")
    claimed_ids = [r["turn_id"] for r in second["rows"] if r.get("state") == "claimed"]
    assert claimed_ids == [t[0] for t in specs]


def test_never_idle_exhaustion_notice(db: None) -> None:
    """Exhaustion posts NOTICE to maestro lanes and refuses with deferred_exhausted."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-exhaust")
    create_thread(thread_id="12286", slug="maestro")
    create_thread(thread_id="21220", slug="maestro-alt")
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    _scan_orion_turn(turn_id=turn_id, turn_number=number, hw=0, now="2020-01-01 00:00:00")
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(claimed["wake_id"])
    retry_at = "2020-01-01 00:00:31"
    sched: dict = {}
    for _ in range(ORION_DEFERRED_STREAMING_MAX_ATTEMPTS + 2):
        sched = record_orion_deferred_streaming(
            wake_id, "exact", 1, None, now=retry_at
        )
        if sched.get("exhausted"):
            break
        retry_at = str(sched["retry_at"])
        reclaimed = claim_due_wakes(now=retry_at)
        assert reclaimed["wake_id"]
        wake_id = str(reclaimed["wake_id"])
    assert sched.get("exhausted")
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
        assert row["state"] == "refused"
        assert row["reason"] == "deferred_exhausted"
        notices = conn.execute(
            "SELECT thread, subject, body FROM turns WHERE subject LIKE 'NOTICE|orion wake%'"
        ).fetchall()
    assert len(notices) == 2
    threads = {str(r["thread"]) for r in notices}
    assert threads == {"12286", "21220"}
    assert str(number) in str(notices[0]["body"])


def test_restart_mid_wait_pending_survives(db: None) -> None:
    """Pending retry_at in SQLite survives re-open (stargate restart surrogate)."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-restart")
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    _scan_orion_turn(turn_id=turn_id, turn_number=number, hw=0, now="2020-01-01 00:00:00")
    wake_id = str(claim_due_wakes(now="2020-01-01 00:00:30")["wake_id"])
    sched = record_orion_deferred_streaming(
        wake_id, "exact", 1, None, now="2020-01-01 00:00:31"
    )
    retry_at = sched["retry_at"]
    assert claim_due_wakes(now="2020-01-01 00:01:00")["wake_id"] is None
    after = claim_due_wakes(now=retry_at)
    assert after["wake_id"]


def test_dispatcher_does_not_refuse_second_deferred(db: None) -> None:
    """Remove terminal refuse: second deferred_streaming stays pending via pipeline record."""
    create_thread(thread_id=_ORION_HOUSE, slug="orion-no-refuse")
    turn_id, _ts, number, _c = insert_turn(
        thread=_ORION_HOUSE,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    _scan_orion_turn(turn_id=turn_id, turn_number=number, hw=0, now="2020-01-01 00:00:00")
    first = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(first["wake_id"])
    record_orion_deferred_streaming(wake_id, "exact", 1, None, now="2020-01-01 00:00:31")
    second = claim_due_wakes(now="2020-01-01 00:01:01")

    class Store:
        def claim(self) -> dict:
            return second

        def subscription(self, agent: str, thread_id: str, *, raw_to=None):
            if agent == "orion":
                return ("exact", _orion_sub())
            return None

        def result(self, **payload: object) -> int:
            raise AssertionError("dispatcher must not record pending deferred twice")

    def _post(*_a, **kwargs):
        record_orion_deferred_streaming(
            str(kwargs["wake_id"]),
            "exact",
            1,
            None,
            now="2020-01-01 00:01:02",
        )
        return ("pending", "deferred_streaming")

    with patch("bus_watch.endpoint_wake_dispatcher._orion_post", side_effect=_post):
        fire_due_wakes(Store())  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["reason"] == "deferred_streaming"
