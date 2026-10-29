"""deferred_streaming: one pending retry, second attempt terminal refused."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)

from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_deferred_streaming_second_attempt_refused(db: None) -> None:
    thread = "21207"
    create_thread(thread_id=thread, slug="orion-retry")
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "orion",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    first_claim = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(first_claim["wake_id"])
    record_wake_result(
        wake_id,
        "pending",
        "deferred_streaming",
        "exact",
        None,
        None,
        now="2020-01-01 00:00:02",
    )

    second_claim = claim_due_wakes(now="2020-01-01 00:01:03")
    assert second_claim["wake_id"]
    rows = [r for r in second_claim["rows"] if r.get("state") == "claimed"]
    assert rows
    assert rows[0].get("reason") == "deferred_streaming"

    sub = {
        "surface": "chatgpt",
        "title": "https://chatgpt.com/c/6ae2e1bc-0c94-83e8-9f21-62467c4935d5",
        "agent_id": "6ae2e1bc-0c94-83e8-9f21-62467c4935d5",
        "id": 1,
        "state": "subscribed",
    }

    class Store:
        def claim(self) -> dict:
            return second_claim

        def subscription(self, agent: str, thread_id: str, *, raw_to=None):
            if agent == "orion":
                return ("exact", sub)
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
        return_value=("pending", "deferred_streaming"),
    ):
        fire_due_wakes(Store())  # type: ignore[arg-type]

    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["reason"] == "deferred_streaming"
