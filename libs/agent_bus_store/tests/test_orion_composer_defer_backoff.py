"""Orion composer-defer bounded backoff uses wake_next_attempt_at (migration_026)."""

from __future__ import annotations

import json

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import (
    ORION_COMPOSER_DEFER_MAX_ATTEMPTS,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from bus_watch.endpoint_wake_composer_defer import COMPOSER_DEFER_ARMED

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    return str(tmp_path / "bus.db")


def _seed_pending_orion(db_path: str) -> tuple[str, str, int]:
    create_thread(thread_id="22388", slug="orion-cdef")
    turn_id, _ts, number, _c = insert_turn(
        thread="22388",
        from_agent="cursor-sdk",
        to_agent="orion",
        subject="FACT|wake",
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
                "thread_id": "22388",
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(claimed["wake_id"])
    assert wake_id
    return wake_id, "2020-01-01 00:00:30", turn_id


def test_composer_defer_release_schedules_next_attempt(db: str) -> None:
    wake_id, stamp, _tid = _seed_pending_orion(db)
    record_wake_result(
        wake_id,
        "pending",
        "composer_not_empty",
        "exact",
        5944,
        None,
        wake_detail={
            "release_claim_for_retry": True,
            "composer_defer_reason": "composer_not_empty",
            "retryable": True,
        },
        now=stamp,
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, wake_next_attempt_at, wake_attempt_count, decided_by_rule "
            "FROM endpoint_wake_ledger WHERE wake_id IS NULL AND agent='orion' LIMIT 1"
        ).fetchone()
    assert row is not None
    assert str(row["state"]) == "pending"
    assert str(row["reason"]) == "composer_not_empty"
    assert str(row["decided_by_rule"]) == COMPOSER_DEFER_ARMED
    assert row["wake_next_attempt_at"] is not None
    assert int(row["wake_attempt_count"] or 0) == 1

    early = claim_due_wakes(now=stamp)
    assert early["wake_id"] is None

    next_stamp = str(row["wake_next_attempt_at"])
    late = claim_due_wakes(now=next_stamp)
    assert late["wake_id"] is not None


def test_composer_defer_exhausts_after_cap(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "agent_bus_store.db.endpoint_wake_ledger.post_orion_wake_undelivered_notices",
        lambda **_: None,
    )
    wake_id, stamp, _tid = _seed_pending_orion(db)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET wake_attempt_count=? WHERE wake_id=?",
            (ORION_COMPOSER_DEFER_MAX_ATTEMPTS, wake_id),
        )
    record_wake_result(
        wake_id,
        "pending",
        "composer_not_empty",
        "exact",
        5944,
        None,
        wake_detail={
            "release_claim_for_retry": True,
            "composer_defer_reason": "composer_not_empty",
        },
        now=stamp,
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE agent='orion' LIMIT 1"
        ).fetchone()
    assert str(row["state"]) == "refused"
    assert str(row["reason"]) == "composer_defer_exhausted"


def test_dispatcher_refused_does_not_overwrite_owner_unknown_paste_receipt(db: str) -> None:
    wake_id, stamp, _tid = _seed_pending_orion(db)
    receipt = {"outcome": "unknown", "reason": "delivery_unconfirmed", "ok": False}
    encoded = f"paste_receipt:{json.dumps(receipt)}"
    record_wake_result(
        wake_id,
        "unknown",
        encoded,
        "exact",
        5944,
        None,
        now=stamp,
    )
    n = record_wake_result(
        wake_id,
        "refused",
        "paste_failed",
        "exact",
        5944,
        None,
        now="2020-01-01 00:01:00",
    )
    assert n == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE agent='orion' LIMIT 1"
        ).fetchone()
    assert str(row["state"]) == "unknown"
    assert str(row["reason"]).startswith("paste_receipt:")
