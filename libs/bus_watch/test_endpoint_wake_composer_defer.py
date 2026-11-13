"""Composer-defer sweep escalation dedup and terminal refused rows."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect

from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_ESCALATED,
    COMPOSER_DEFER_ESCALATED_NOTICED,
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
                "2020-01-01 00:00:00",
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
                "2020-01-01 00:00:00",
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


def test_escalate_many_rows_one_turn_single_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-dedup-one-turn")
    assert detail is not None
    thread = str(detail["id"])
    shared_turn = 42
    for _ in range(3):
        _seed_sweep_eligible_row(thread, turn_number=shared_turn)
    store = _RecordingStore()
    refused = escalate_composer_deferred_on_sweep(store)
    assert refused == 3
    assert len(store.posts) == 1
    assert store.posts[0]["to_agent"] == "maestro"
    assert store.posts[0]["subject"] == _ESCALATION_SUBJECT
    body = store.posts[0]["body"]
    assert "count=3" in body
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, state, decided_by_rule FROM endpoint_wake_ledger "
            "WHERE thread_id=? ORDER BY id",
            (thread,),
        ).fetchall()
    assert len(rows) == 3
    assert all(r["state"] == "refused" for r in rows)
    noticed = [
        r for r in rows if r["decided_by_rule"] == COMPOSER_DEFER_ESCALATED_NOTICED
    ]
    escalated = [r for r in rows if r["decided_by_rule"] == COMPOSER_DEFER_ESCALATED]
    assert len(noticed) == 1
    assert len(escalated) == 2
    assert int(noticed[0]["id"]) == int(rows[0]["id"])


def test_escalate_second_pass_no_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-second-pass")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=7)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 1
    assert escalate_composer_deferred_on_sweep(store) == 0
    assert len(store.posts) == 1


def test_escalate_two_turns_two_notices(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-two-turns")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=1)
    _seed_sweep_eligible_row(thread, turn_number=2)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 2
    assert len(store.posts) == 2
    subjects = {p["subject"] for p in store.posts}
    assert subjects == {_ESCALATION_SUBJECT}


def test_notify_empty_rearms_escalated(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-rearm")
    assert detail is not None
    thread = str(detail["id"])
    _seed_sweep_eligible_row(thread, turn_number=9)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 1
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


def test_escalate_new_row_after_prior_marker_no_second_notice(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-durable-marker")
    assert detail is not None
    thread = str(detail["id"])
    turn_num = 55
    for _ in range(3):
        _seed_sweep_eligible_row(thread, turn_number=turn_num)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 3
    assert len(store.posts) == 1
    _seed_sweep_eligible_row(thread, turn_number=turn_num)
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 1
    with connect() as conn:
        n_refused = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger "
            "WHERE thread_id=? AND turn_number=? AND state='refused'",
            (thread, turn_num),
        ).fetchone()
    assert int(n_refused["n"]) == 4


def test_escalate_two_agents_one_turn_id_one_notice(db: None) -> None:
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
    id_a = _seed_sweep_eligible_same_turn_id(
        thread, turn_id=turn_id, turn_number=number, agent="grok-bot-gc1"
    )
    id_b = _seed_sweep_eligible_same_turn_id(
        thread, turn_id=turn_id, turn_number=number, agent="grok-bot-gc2"
    )
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 2
    assert len(store.posts) == 1
    body = str(store.posts[0]["body"])
    assert "grok-bot-gc1" in body
    assert "grok-bot-gc2" in body
    assert f"id={id_a}" in body
    assert f"id={id_b}" in body
    assert "count=2" in body
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=? "
            "ORDER BY id",
            (thread,),
        ).fetchall()
    assert len(rows) == 2
    rep_id = min(id_a, id_b)
    by_id = {int(r["id"]): r["decided_by_rule"] for r in rows}
    assert by_id[rep_id] == COMPOSER_DEFER_ESCALATED_NOTICED
    other_id = id_a + id_b - rep_id
    assert by_id[other_id] == COMPOSER_DEFER_ESCALATED


def test_escalate_create_turn_raises_rows_still_refused(db: None) -> None:
    detail = create_thread(thread_id=None, slug="b1-stamp-fail")
    assert detail is not None
    thread = str(detail["id"])
    turn_num = 3
    _seed_sweep_eligible_row(thread, turn_number=turn_num)

    class _BrokenStore:
        def create_turn(self, **kwargs: object) -> dict:
            raise RuntimeError("stamp failed")

    refused = escalate_composer_deferred_on_sweep(_BrokenStore())
    assert refused == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["decided_by_rule"] == COMPOSER_DEFER_ESCALATED
    _seed_sweep_eligible_row(thread, turn_number=turn_num)
    store = _RecordingStore()
    assert escalate_composer_deferred_on_sweep(store) == 1
    assert len(store.posts) == 1
    with connect() as conn:
        noticed = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger "
            "WHERE thread_id=? AND decided_by_rule=?",
            (thread, COMPOSER_DEFER_ESCALATED_NOTICED),
        ).fetchone()
    assert int(noticed["n"]) == 1
