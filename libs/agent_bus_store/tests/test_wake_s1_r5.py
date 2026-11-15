"""a:43821 S1-tests round 5 (G1, G2, G4, G5 + behavior pins)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_attempts import (
    DISPLAY_COMPOSER_DEFER_MAX_WALL_S,
    composer_defer_over_cap,
    display_composer_defer_retry_delay_s,
    schedule_composer_defer_retry,
)
from agent_bus_store.db.endpoint_wake_ledger import (
    CONSUMER,
    advance_scan,
    claim_due_wakes,
    get_cursor,
    record_wake_result,
)
from agent_bus_store.testing.wake_cursor_test_support import rewind_wake_cursor_for_test
from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_ARMED,
    COMPOSER_DEFER_SWEEP,
    escalate_composer_deferred_on_sweep,
    finalize_composer_defer_pass,
    notify_composer_empty_for_target,
)
from bus_watch.endpoint_wake_dispatcher import fire_due_wakes

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _shift(stamp: str, seconds: int) -> str:
    dt = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    return (dt + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


class _RecordingStore:
    posts: list[dict]

    def __init__(self) -> None:
        self.posts = []

    def create_turn(self, **kwargs: object) -> dict:
        self.posts.append(dict(kwargs))
        return {"ok": True}


def _seed_display_pending(
    thread: str,
    *,
    sub_id: int = 91,
    scan_stamp: str = "2030-06-01 00:00:00",
) -> tuple[int, int]:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="R5|display",
        body="wake",
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
                "delivery_agent_id": "tab-r5",
            }
        ],
        now=scan_stamp,
    )
    return turn_id, number


def test_composer_defer_record_wake_result_backoff_real_path_n1_through_n4(
    db: None,
) -> None:
    """Break: record_wake_result owns next=stamp+delay(n); sweep must not double-count."""
    detail = create_thread(thread_id=None, slug="r5-g2-real-path")
    assert detail is not None
    thread = str(detail["id"])
    _seed_display_pending(thread)
    store = _RecordingStore()
    cur = "2030-06-01 00:00:11"
    for attempt in range(1, 5):
        if attempt > 1:
            assert escalate_composer_deferred_on_sweep(store, stamp=cur) == 0
        claimed = claim_due_wakes(now=cur)
        assert claimed["wake_id"], f"attempt {attempt}: claim at {cur}"
        wake_id = str(claimed["wake_id"])
        record_stamp = _shift(cur, 1)
        record_wake_result(
            wake_id,
            "pending",
            "composer_not_empty",
            "exact",
            91,
            None,
            wake_detail={
                "release_claim_for_retry": True,
                "composer_defer_reason": "composer_not_empty",
            },
            now=record_stamp,
        )
        delay = display_composer_defer_retry_delay_s(attempt)
        expected_next = _shift(record_stamp, delay)
        with connect() as conn:
            row = conn.execute(
                "SELECT wake_next_attempt_at, wake_attempt_count, decided_by_rule "
                "FROM endpoint_wake_ledger WHERE thread_id=?",
                (thread,),
            ).fetchone()
        assert row is not None
        assert str(row["wake_next_attempt_at"]) == expected_next
        assert int(row["wake_attempt_count"] or 0) == attempt
        assert row["decided_by_rule"] == COMPOSER_DEFER_ARMED
        assert finalize_composer_defer_pass() == 1
        assert escalate_composer_deferred_on_sweep(store, stamp=record_stamp) == 0
        with connect() as conn:
            after_sweep = conn.execute(
                "SELECT wake_attempt_count, decided_by_rule, wake_next_attempt_at "
                "FROM endpoint_wake_ledger WHERE thread_id=?",
                (thread,),
            ).fetchone()
        assert int(after_sweep["wake_attempt_count"] or 0) == attempt
        assert after_sweep["decided_by_rule"] == COMPOSER_DEFER_SWEEP
        cur = expected_next


def test_pin_schedule_composer_defer_retry_leaves_wake_next_attempt_at_null(
    db: None,
) -> None:
    """Pin intended ownership: sweep helper does not set wake_next_attempt_at."""
    detail = create_thread(thread_id=None, slug="r5-pin-schedule-null")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="PIN|sched",
        body="b",
    )
    stamp = "2030-07-01 00:00:00"
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, "
            "decided_by_rule, wake_next_attempt_at, wake_attempt_count) "
            "VALUES (?,?,?,?, 'pending', 'composer_not_empty', ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                COMPOSER_DEFER_SWEEP,
                "2030-07-01 00:05:00",
                2,
            ),
        )
        row = conn.execute(
            "SELECT id, agent, thread_id, turn_number, reason, decided_by_rule, "
            "wake_next_attempt_at, wake_attempt_count FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
        assert row is not None
        before_next = row["wake_next_attempt_at"]
        updated = schedule_composer_defer_retry(conn, [dict(row)], stamp=stamp)
        assert updated == 1
        after = conn.execute(
            "SELECT decided_by_rule, wake_next_attempt_at FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert after is not None
    assert after["decided_by_rule"] is None
    assert after["wake_next_attempt_at"] == before_next


def test_g4_sweep_skips_future_wake_next_attempt_at_fixed_on_master(
    db: None,
) -> None:
    """Break (overlay d017e156): pre-fix sweeps churned rows before due time."""
    detail = create_thread(thread_id=None, slug="r5-g4-gate")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="G4|gate",
        body="b",
    )
    future = "2030-08-01 01:00:00"
    early = "2030-08-01 00:00:00"
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, "
            "decided_by_rule, wake_next_attempt_at, wake_attempt_count) "
            "VALUES (?,?,?,?, 'pending', 'composer_not_empty', ?, ?, 0)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                COMPOSER_DEFER_SWEEP,
                future,
            ),
        )
    store = _RecordingStore()
    for _ in range(2):
        assert escalate_composer_deferred_on_sweep(store, stamp=early) == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule, wake_attempt_count, wake_next_attempt_at "
            "FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row is not None
    assert row["decided_by_rule"] == COMPOSER_DEFER_SWEEP
    assert int(row["wake_attempt_count"] or 0) == 0
    assert str(row["wake_next_attempt_at"]) == future
    assert len(store.posts) == 0


def test_g1_display_row_full_pass_advancing_clock(db: None) -> None:
    """Break: display delivery path must complete after backoff + composer-empty."""
    sub_id = 7101
    tab = "tab-g1-full-pass"
    detail = create_thread(thread_id=None, slug="r5-g1-full-pass")
    assert detail is not None
    thread = str(detail["id"])
    _seed_display_pending(thread, sub_id=sub_id, scan_stamp="2031-01-01 00:00:00")
    post_attempts = 0

    def _display_post(*_a: object, **_k: object) -> tuple[str, str]:
        nonlocal post_attempts
        post_attempts += 1
        if post_attempts == 1:
            return ("pending", "composer_not_empty")
        return ("delivered", "delivered")

    def _make_store(claimed: dict[str, object], *, now: str) -> object:
        class Store:
            def claim(self) -> dict:
                return claimed  # type: ignore[return-value]

            def subscription(self, agent: str, thread_id: str):
                if agent == "grok-bot-gc1" and thread_id == thread:
                    return (
                        "exact",
                        {
                            "id": sub_id,
                            "surface": "grok",
                            "display": "9",
                            "title": "gc1",
                            "agent_id": tab,
                        },
                    )
                return None

            def result(self, **payload: object) -> int:
                return record_wake_result(
                    str(payload["wake_id"]),
                    str(payload["outcome"]),
                    str(payload["reason"]) if payload["reason"] is not None else None,
                    str(payload["route"]) if payload["route"] is not None else None,
                    payload["subscription_id"],  # type: ignore[arg-type]
                    payload["cse_registration_id"],  # type: ignore[arg-type]
                    decided_by_rule=payload.get("decided_by_rule"),  # type: ignore[arg-type]
                    wake_detail=payload.get("wake_detail"),  # type: ignore[arg-type]
                    now=now,
                )

        return Store()

    t0 = "2031-01-01 00:00:10"
    claimed1 = claim_due_wakes(now=t0)
    assert claimed1["wake_id"]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_display_post,
    ):
        fire_due_wakes(_make_store(claimed1, now=t0))  # type: ignore[arg-type]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, wake_next_attempt_at FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["wake_next_attempt_at"]
    finalize_composer_defer_pass()
    escalate_composer_deferred_on_sweep(_RecordingStore(), stamp="2031-01-01 00:00:20")
    notify_composer_empty_for_target(
        display="9",
        surface="grok",
        agent_id=tab,
        thread_scope=thread,
    )
    next_stamp = str(row["wake_next_attempt_at"])
    claimed2 = claim_due_wakes(now=next_stamp)
    assert claimed2["wake_id"]
    with patch(
        "bus_watch.endpoint_wake_dispatcher._display_post",
        side_effect=_display_post,
    ):
        fire_due_wakes(_make_store(claimed2, now=next_stamp))  # type: ignore[arg-type]
    assert post_attempts == 2
    with connect() as conn:
        final = conn.execute(
            "SELECT state FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert final["state"] == "delivered"


def test_g5_notify_composer_empty_enables_claim_for_armed_pending(db: None) -> None:
    """Break: composer-empty must clear armed tags so claim is non-vacuous."""
    detail = create_thread(thread_id=None, slug="r5-g5-non-vacuous")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="G5|empty",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, decided_by_rule) "
            "VALUES (?,?,?,?, 'pending', 'composer_not_empty', ?)",
            ("grok-bot-gc1", thread, turn_id, number, COMPOSER_DEFER_ARMED),
        )
    stamp = "2032-03-01 12:00:00"
    assert claim_due_wakes(now=stamp)["wake_id"] is None
    cleared = notify_composer_empty_for_target(
        display="9",
        surface="grok",
        agent_id="grok-bot-gc1",
        thread_scope=thread,
    )
    assert cleared == 1
    claimed = claim_due_wakes(now=stamp)
    assert claimed["wake_id"]
    claimed_turns = {int(r["turn_id"]) for r in claimed["rows"]}
    assert turn_id in claimed_turns
    assert (
        notify_composer_empty_for_target(
            display="9",
            surface="grok",
            agent_id="grok-bot-gc1",
            thread_scope=thread,
        )
        == 0
    )


def test_pin_composer_defer_wall_cap_2h_with_young_attempt_count() -> None:
    """Pin: 2h wall triggers drop even when wake_attempt_count is still young."""
    row = {
        "wake_attempt_count": 0,
        "enqueued_at": "2030-01-01 00:00:00",
        "created_at": "2030-01-01 00:00:00",
    }
    young = composer_defer_over_cap(row, stamp="2030-01-01 00:30:00")
    at_wall = composer_defer_over_cap(
        row,
        stamp=_shift("2030-01-01 00:00:00", DISPLAY_COMPOSER_DEFER_MAX_WALL_S),
    )
    assert young is False
    assert at_wall is True


def test_pin_escalate_wildcard_subscription_groups_one_retry(db: None) -> None:
    """Pin: rows sharing subscription_id get one sweep retry, not per-row churn."""
    detail = create_thread(thread_id=None, slug="r5-pin-wildcard-sub")
    assert detail is not None
    thread = str(detail["id"])
    sub_id = 8800
    shared_turn = 33
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="SUB|shared",
        body="b",
    )
    stamp = "2033-01-01 00:00:00"
    for agent in ("grok-bot-gc1", "grok-bot-gc2"):
        with write_connect() as conn:
            conn.execute(
                "INSERT INTO endpoint_wake_ledger "
                "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
                "enqueued_at, subscription_id, decided_by_rule, wake_attempt_count) "
                "VALUES (?,?,?,?, 'pending', 'composer_not_empty', ?, ?, ?, ?, 0)",
                (
                    agent,
                    thread,
                    turn_id,
                    shared_turn,
                    stamp,
                    stamp,
                    sub_id,
                    COMPOSER_DEFER_SWEEP,
                ),
            )
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store, stamp=stamp) == 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, state, decided_by_rule, wake_next_attempt_at "
            "FROM endpoint_wake_ledger WHERE thread_id=? ORDER BY id",
            (thread,),
        ).fetchall()
    assert len(rows) == 2
    assert all(r["state"] == "pending" for r in rows)
    assert sum(1 for r in rows if r["decided_by_rule"] is None) == 1
    assert sum(1 for r in rows if r["decided_by_rule"] == COMPOSER_DEFER_SWEEP) == 1
    assert all(r["wake_next_attempt_at"] is None for r in rows)


def test_pin_refused_pending_armed_rule_blocks_claim_until_notify(db: None) -> None:
    """Pin: refused-with-armed-rule rows stay terminal until empty-notify rearm."""
    from bus_watch.endpoint_wake_composer_defer import COMPOSER_DEFER_ESCALATED

    detail = create_thread(thread_id=None, slug="r5-pin-refused-armed")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="PIN|refused",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, decided_by_rule, "
            "finished_at) VALUES (?,?,?,?, 'refused', 'composer_not_empty', ?, "
            "datetime('now'))",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                COMPOSER_DEFER_ESCALATED,
            ),
        )
    stamp = "2034-01-01 00:00:00"
    assert claim_due_wakes(now=stamp)["wake_id"] is None
    assert (
        notify_composer_empty_for_target(
            display="9",
            surface="grok",
            agent_id="grok-bot-gc1",
            thread_scope=thread,
        )
        == 1
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule, finished_at, wake_next_attempt_at "
            "FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["decided_by_rule"] is None
    assert row["finished_at"] is None
    assert row["wake_next_attempt_at"] is None
