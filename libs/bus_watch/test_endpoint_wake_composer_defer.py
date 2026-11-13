"""Composer-defer sweep escalation dedup and terminal refused rows."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import _now

from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_ESCALATED,
    COMPOSER_DEFER_SWEEP,
    escalate_composer_deferred_on_sweep,
    notify_composer_empty_for_target,
)

_ESCALATION_SUBJECT = "NOTICE|endpoint wake composer defer exhausted"

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _seed_sweep_eligible_row(
    thread: str,
    *,
    turn_number: int | None = None,
    reason: str = "composer_not_empty",
) -> tuple[int, int]:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject=f"SWEEP|{reason}",
        body="sweep",
    )
    if turn_number is not None:
        number = turn_number
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule) VALUES (?,?,?,?, 'pending', ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                reason,
                _now(None),
                COMPOSER_DEFER_SWEEP,
            ),
        )
    return turn_id, number


def _seed_sweep_eligible_same_turn_id(
    thread: str,
    *,
    turn_id: int,
    turn_number: int,
    agent: str,
    reason: str = "composer_not_empty",
) -> int:
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule) VALUES (?,?,?,?, 'pending', ?, ?, ?)",
            (
                agent,
                thread,
                turn_id,
                turn_number,
                reason,
                _now(None),
                COMPOSER_DEFER_SWEEP,
            ),
        )
        return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def _notify_kw(thread: str) -> dict[str, str]:
    return dict(
        display="9",
        surface="grok",
        agent_id="grok-bot-gc1",
        thread_scope=thread,
    )


class _RecordingStore:
    posts: list[dict]

    def __init__(self) -> None:
        self.posts = []

    def create_turn(self, **kwargs: object) -> dict:
        self.posts.append(dict(kwargs))
        return {"ok": True}


def test_escalate_many_rows_one_turn_schedules_retry_not_refuse(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-dedup-one-turn")
    assert detail is not None
    thread = str(detail["id"])
    shared_turn = 42
    for _ in range(3):
        _seed_sweep_eligible_row(thread, turn_number=shared_turn)
    store = _RecordingStore()
    dropped = escalate_composer_deferred_on_sweep(store)
    assert dropped == 0
    assert len(store.posts) == 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, state, decided_by_rule, wake_next_attempt_at FROM endpoint_wake_ledger "
            "WHERE thread_id=? ORDER BY id",
            (thread,),
        ).fetchall()
    assert len(rows) == 3
    assert all(r["state"] == "pending" for r in rows)
    assert sum(1 for r in rows if r["wake_next_attempt_at"]) == 3


def test_escalate_drop_at_cap_one_notice_per_row(db: None) -> None:
    from agent_bus_store.db.endpoint_wake_attempts import (
        COMPOSER_DEFER_DROP_NOTICED,
        DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS,
    )

    detail = create_thread(thread_id=None, slug="b1-drop-cap")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=88)
    with connect() as conn:
        row_id = int(
            conn.execute(
                "SELECT id FROM endpoint_wake_ledger WHERE thread_id=? LIMIT 1",
                (thread,),
            ).fetchone()["id"]
        )
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET wake_attempt_count=? WHERE thread_id=?",
            (DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS, thread),
        )
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 1
    assert store.posts[0]["subject"] == _ESCALATION_SUBJECT
    assert f"wake_row_id={row_id}" in store.posts[0]["body"]
    with connect() as conn:
        row = conn.execute(
            "SELECT state, drop_reason, decided_by_rule FROM endpoint_wake_ledger WHERE id=?",
            (row_id,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["drop_reason"] == "dropped_cap"
    assert row["decided_by_rule"] == COMPOSER_DEFER_DROP_NOTICED
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(store.posts) == 1


def test_escalate_second_pass_retries_without_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-second-pass")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=7)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 0
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=? WHERE thread_id=?",
            (COMPOSER_DEFER_SWEEP, thread),
        )
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(store.posts) == 0


def test_escalate_two_turns_no_notice_until_drop(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-two-turns")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=1)
    _seed_sweep_eligible_row(thread, turn_number=2)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(store.posts) == 0


def test_notify_empty_rearms_escalated(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-rearm")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=9)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 0
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET state='refused', decided_by_rule=? "
            "WHERE thread_id=? AND turn_number=9",
            (COMPOSER_DEFER_ESCALATED, thread),
        )
    _seed_sweep_eligible_row(thread, turn_number=10)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=? "
            "WHERE thread_id=? AND turn_number=10",
            ("composer_defer:armed", thread),
        )
    cleared = notify_composer_empty_for_target(**_notify_kw(thread))
    assert cleared == 2
    with connect() as conn:
        rearmed = conn.execute(
            "SELECT state, decided_by_rule, finished_at FROM endpoint_wake_ledger "
            "WHERE thread_id=? AND turn_number=9",
            (thread,),
        ).fetchone()
    assert rearmed["state"] == "pending"
    assert rearmed["decided_by_rule"] is None
    assert rearmed["finished_at"] is None


def test_escalate_new_row_after_prior_retry_no_second_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-durable-marker")
    assert detail is not None
    thread = str(detail["id"])
    turn_num = 55
    _seed_sweep_eligible_row(thread, turn_number=turn_num)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 0
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=?, wake_attempt_count=12 "
            "WHERE thread_id=? AND turn_number=?",
            (COMPOSER_DEFER_SWEEP, thread, turn_num),
        )
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 1
    _seed_sweep_eligible_row(thread, turn_number=turn_num)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET decided_by_rule=?, wake_attempt_count=12 "
            "WHERE thread_id=? AND turn_number=? AND state='pending'",
            (COMPOSER_DEFER_SWEEP, thread, turn_num),
        )
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 2


def test_escalate_two_agents_one_turn_id_retries_separately(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-two-agents-one-turn")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="SWEEP|shared",
        body="shared",
    )
    _seed_sweep_eligible_same_turn_id(
        thread, turn_id=turn_id, turn_number=number, agent="grok-bot-gc1"
    )
    _seed_sweep_eligible_same_turn_id(
        thread, turn_id=turn_id, turn_number=number, agent="grok-bot-gc2"
    )
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(store.posts) == 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, state, wake_next_attempt_at FROM endpoint_wake_ledger WHERE thread_id=? "
            "ORDER BY id",
            (thread,),
        ).fetchall()
    assert len(rows) == 2
    assert all(r["state"] == "pending" for r in rows)
    assert all(r["wake_next_attempt_at"] for r in rows)


def test_escalate_create_turn_raises_row_still_dropped(db: None) -> None:
    from agent_bus_store.db.endpoint_wake_attempts import (
        COMPOSER_DEFER_DROP_NOTICED,
        DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS,
    )

    detail = create_thread(thread_id=None, slug="b1-stamp-fail")
    assert detail is not None
    thread = str(detail["id"])
    turn_num = 3
    _seed_sweep_eligible_row(thread, turn_number=turn_num)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_ledger SET wake_attempt_count=? WHERE thread_id=?",
            (DISPLAY_COMPOSER_DEFER_MAX_ATTEMPTS, thread),
        )

    class _BrokenStore:
        def create_turn(self, **kwargs: object) -> dict:
            raise RuntimeError("stamp failed")

    dropped = escalate_composer_deferred_on_sweep(_BrokenStore())
    assert dropped == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["decided_by_rule"] == COMPOSER_DEFER_DROP_NOTICED
