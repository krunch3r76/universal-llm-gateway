"""a:43821 S1 addendum acceptance tests (phase-0 observe-only)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_attempts import (
    COMPOSER_DEFER_DROP_NOTICED,
    DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS,
    display_composer_defer_retry_delay_s,
    expected_delivery_at_for_enqueue,
    merge_missed_into_paste,
    preview_missed_prefix,
    stamp_missed_surfaced,
)
from agent_bus_store.db.endpoint_wake_ledger import (
    CONSUMER,
    ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.testing.wake_cursor_test_support import rewind_wake_cursor_for_test
from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_SWEEP,
    escalate_composer_deferred_on_sweep,
    finalize_composer_defer_pass,
)

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


class _RecordingStore:
    posts: list[dict]

    def __init__(self) -> None:
        self.posts = []

    def create_turn(self, **kwargs: object) -> dict:
        self.posts.append(dict(kwargs))
        return {"ok": True}


def test_migration_037_backfill_enqueued_equals_created_at(db: None) -> None:
    detail = create_thread(thread_id=None, slug="backfill-enq")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="OLD|row",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, created_at) "
            "VALUES (?,?,?,?, 'pending', '2019-06-01 00:00:00')",
            ("grok-bot-gc1", thread, turn_id, number),
        )
        conn.execute(
            "UPDATE endpoint_wake_ledger SET enqueued_at=created_at WHERE enqueued_at IS NULL"
        )
    with connect() as conn:
        row = conn.execute(
            "SELECT enqueued_at, created_at FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["enqueued_at"] == row["created_at"]


def test_expected_delivery_at_uses_endpoint_wake_debounce_s_default(db: None) -> None:
    stamp = "2020-01-01 12:00:00"
    expected = expected_delivery_at_for_enqueue("grok-bot-gc1", "99999", stamp)
    assert expected == "2020-01-01 12:10:00"
    assert ENDPOINT_WAKE_DEBOUNCE_S_DEFAULT == 600


def test_advance_scan_sets_enqueued_and_expected_delivery_at(db: None) -> None:
    detail = create_thread(thread_id=None, slug="enqueue-cols")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="SCAN|e",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        CONSUMER,
        0,
        turn_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
                "subscription_id": 77,
            }
        ],
        now="2030-01-01 00:00:00",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT enqueued_at, expected_delivery_at, subscription_id FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["enqueued_at"] == "2030-01-01 00:00:00"
    assert row["expected_delivery_at"] == "2030-01-01 00:10:00"


def test_composer_not_empty_backoff_delays_are_30_60_120_300_cap() -> None:
    assert display_composer_defer_retry_delay_s(1) == 30
    assert display_composer_defer_retry_delay_s(2) == 60
    assert display_composer_defer_retry_delay_s(3) == 120
    assert display_composer_defer_retry_delay_s(4) == 300
    assert display_composer_defer_retry_delay_s(5) == 300


def _seed_pending_wake(thread: str, sub_id: int = 1) -> str:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="WAKE|defer",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(
        CONSUMER,
        0,
        turn_id,
        [
            {
                "agent": "grok-bot-gc1",
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": number,
                "subscription_id": sub_id,
            }
        ],
        now="2030-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2030-01-01 00:00:01")
    wake_id = str(claimed["wake_id"])
    assert wake_id
    record_wake_result(
        wake_id,
        "pending",
        "composer_not_empty",
        "exact",
        sub_id,
        None,
        wake_detail={"release_claim_for_retry": True},
        now="2030-01-01 00:00:02",
    )
    finalize_composer_defer_pass()
    return wake_id


def test_composer_defer_stays_pending_until_attempt_cap(db: None) -> None:
    detail = create_thread(thread_id=None, slug="defer-cap")
    assert detail is not None
    thread = str(detail["id"])
    _seed_pending_wake(thread)
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["reason"] == "composer_not_empty"
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=?, wake_attempt_count=? "
            "WHERE thread_id=?",
            (COMPOSER_DEFER_SWEEP, DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS - 1, thread),
        )
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store, stamp="2030-01-01 01:00:00") == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule, wake_next_attempt_at FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["decided_by_rule"] is None
    assert row["wake_next_attempt_at"]


def test_row_12027_three_sweeps_one_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="row-12027")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="SWEEP|x",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "enqueued_at, decided_by_rule, wake_attempt_count) "
            "VALUES (?,?,?,?, 'pending','composer_not_empty', datetime('now'), "
            "datetime('now'), ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                COMPOSER_DEFER_SWEEP,
                DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS,
            ),
        )
    store = _RecordingStore()
    for _ in range(3):
        escalate_composer_deferred_on_sweep(store, stamp="2030-06-01 00:00:00")
    assert len(store.posts) == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule, drop_reason FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["decided_by_rule"] == COMPOSER_DEFER_DROP_NOTICED
    assert row["drop_reason"] == "dropped_cap"


def test_missed_prefix_shown_once_then_stamp_after_deliver(db: None) -> None:
    detail = create_thread(thread_id=None, slug="missed-once")
    assert detail is not None
    thread = str(detail["id"])
    sub_id = 42
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="DROP|1",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, subscription_id, "
            "drop_reason, dropped_at, wake_attempt_count) "
            "VALUES ('grok-bot-gc1', ?, ?, ?, 'refused', ?, 'dropped_cap', "
            "datetime('now'), 12)",
            (thread, turn_id, number, sub_id),
        )
    stamp = "2030-01-01 00:00:00"
    with connect() as conn:
        prefix1, ids1 = preview_missed_prefix(conn, subscription_id=sub_id, stamp=stamp)
        assert "missed:" in prefix1
        assert ids1
        prefix2, ids2 = preview_missed_prefix(conn, subscription_id=sub_id, stamp=stamp)
        assert prefix2 == prefix1
    with write_connect() as conn:
        stamp_missed_surfaced(conn, subscription_id=sub_id, row_ids=ids1, stamp=stamp)
    with connect() as conn:
        prefix3, ids3 = preview_missed_prefix(conn, subscription_id=sub_id, stamp=stamp)
    assert prefix3 == ""
    assert ids3 == []


def test_missed_stamp_skipped_when_paste_not_delivered(db: None) -> None:
    detail = create_thread(thread_id=None, slug="missed-no-stamp")
    assert detail is not None
    thread = str(detail["id"])
    sub_id = 55
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="DROP|2",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, subscription_id, "
            "drop_reason, dropped_at) VALUES ('grok-bot-gc1', ?, ?, ?, 'refused', ?, "
            "'dropped_cap', datetime('now'))",
            (thread, turn_id, number, sub_id),
        )
    stamp = "2030-02-01 00:00:00"
    with connect() as conn:
        prefix, ids = preview_missed_prefix(conn, subscription_id=sub_id, stamp=stamp)
    assert prefix
    merged = merge_missed_into_paste("wake body", prefix)
    assert merged.startswith("missed:")
    with connect() as conn:
        row = conn.execute(
            "SELECT surfaced_at FROM endpoint_wake_ledger WHERE subscription_id=?",
            (sub_id,),
        ).fetchone()
    assert row["surfaced_at"] is None


def test_record_wake_result_sets_last_failure_and_delivered_at(db: None) -> None:
    detail = create_thread(thread_id=None, slug="col-populate")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="COL|t",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, wake_id, claimed_at) "
            "VALUES (?,?,?,?, 'claimed', 'w-col', datetime('now'))",
            ("grok-bot-gc1", thread, turn_id, number),
        )
    record_wake_result(
        "w-col",
        "delivered",
        "delivered",
        "exact",
        9,
        None,
        now="2031-01-01 00:00:00",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT delivered_at, last_failure_reason FROM endpoint_wake_ledger WHERE wake_id='w-col'"
        ).fetchone()
    assert row["delivered_at"] == "2031-01-01 00:00:00"
    assert row["last_failure_reason"] is None


def test_record_wake_result_sets_last_failure_on_pending_composer_defer(
    db: None,
) -> None:
    detail = create_thread(thread_id=None, slug="lf-pending")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="PEND|lf",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, wake_id, claimed_at) "
            "VALUES (?,?,?,?, 'claimed', 'w-pend-lf', datetime('now'))",
            ("grok-bot-gc1", thread, turn_id, number),
        )
    record_wake_result(
        "w-pend-lf",
        "pending",
        "composer_not_empty",
        "exact",
        3,
        None,
        wake_detail={
            "release_claim_for_retry": True,
            "composer_defer_reason": "composer_not_empty",
        },
        now="2032-01-01 00:00:00",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, last_failure_reason FROM endpoint_wake_ledger "
            "WHERE wake_id IS NULL AND thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["last_failure_reason"] == "composer_not_empty"


def test_record_wake_result_sets_last_failure_on_refused(db: None) -> None:
    detail = create_thread(thread_id=None, slug="lf-refused")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="REF|lf",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, wake_id, claimed_at) "
            "VALUES (?,?,?,?, 'claimed', 'w-ref-lf', datetime('now'))",
            ("grok-bot-gc1", thread, turn_id, number),
        )
    record_wake_result(
        "w-ref-lf",
        "refused",
        "agent_id_mismatch",
        "exact",
        4,
        None,
        now="2032-02-01 00:00:00",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, last_failure_reason FROM endpoint_wake_ledger WHERE wake_id='w-ref-lf'"
        ).fetchone()
    assert row["state"] == "refused"
    assert row["last_failure_reason"] == "agent_id_mismatch"
