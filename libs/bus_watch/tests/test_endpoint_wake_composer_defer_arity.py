"""Composer-defer SQL placeholder arity vs COMPOSER_DEFER_REASONS."""

from __future__ import annotations

import inspect

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import claim_due_wakes
from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_ARMED,
    COMPOSER_DEFER_ESCALATED,
    COMPOSER_DEFER_REASONS,
    COMPOSER_DEFER_SWEEP,
    LEGACY_NORMALIZE_AGENT_MISMATCH,
    composer_defer_reason_in_sql,
    composer_defer_reasons_sorted,
    finalize_composer_defer_pass,
    normalize_legacy_pending_wakes,
    notify_composer_empty_for_target,
)

_CLAIM_NOW = "2020-01-01 12:00:00"

pytestmark = pytest.mark.offline


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _seed_pending_composer_defer(
    thread: str,
    *,
    reason: str,
    decided_by_rule: str | None = None,
) -> None:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject=f"DEFER|{reason}",
        body="defer",
    )
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
                decided_by_rule,
            ),
        )


def test_composer_defer_reason_in_sql_matches_set_size() -> None:
    assert composer_defer_reason_in_sql().count("?") == len(COMPOSER_DEFER_REASONS)
    assert len(composer_defer_reasons_sorted()) == len(COMPOSER_DEFER_REASONS)


def test_composer_defer_module_has_no_three_placeholder_in_clauses() -> None:
    import bus_watch.endpoint_wake_composer_defer as mod

    source = inspect.getsource(mod)
    assert "IN (?,?,?)" not in source


def test_normalize_legacy_pending_wakes_all_four_reasons(db: None) -> None:
    detail = create_thread(thread_id=None, slug="arity-normalize")
    assert detail is not None
    thread = str(detail["id"])
    for reason in sorted(COMPOSER_DEFER_REASONS):
        _seed_pending_composer_defer(thread, reason=reason)
    stats = normalize_legacy_pending_wakes()
    assert stats["composer_defer_armed"] == len(COMPOSER_DEFER_REASONS)
    with connect() as conn:
        armed = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger "
            "WHERE state='pending' AND decided_by_rule=?",
            (COMPOSER_DEFER_ARMED,),
        ).fetchone()
    assert int(armed["n"]) == len(COMPOSER_DEFER_REASONS)


def test_finalize_composer_defer_pass_includes_non_empty_composer(db: None) -> None:
    detail = create_thread(thread_id=None, slug="arity-finalize")
    assert detail is not None
    thread = str(detail["id"])
    _seed_pending_composer_defer(
        thread, reason="non_empty_composer", decided_by_rule=COMPOSER_DEFER_ARMED
    )
    assert finalize_composer_defer_pass() == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["decided_by_rule"] == COMPOSER_DEFER_SWEEP


def test_notify_composer_empty_for_target_includes_non_empty_composer(db: None) -> None:
    detail = create_thread(thread_id=None, slug="arity-notify")
    assert detail is not None
    thread = str(detail["id"])
    _seed_pending_composer_defer(
        thread,
        reason="non_empty_composer",
        decided_by_rule="composer_defer:armed",
    )
    cleared = notify_composer_empty_for_target(
        display="9",
        surface="grok",
        agent_id="grok-bot-gc1",
        thread_scope=thread,
    )
    assert cleared == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["decided_by_rule"] is None


def _seed_escalated_composer_defer(
    thread: str, *, reason: str = "composer_not_empty"
) -> tuple[int, int]:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject=f"DEFER|{reason}",
        body="defer",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule, finished_at) VALUES (?,?,?,?, 'refused', ?, ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                reason,
                "2020-01-01 00:00:00",
                COMPOSER_DEFER_ESCALATED,
                "2020-01-01 00:00:01",
            ),
        )
    return turn_id, number


def _seed_refused_composer_without_escalation(
    thread: str,
    *,
    decided_by_rule: str | None,
) -> None:
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="DEFER|composer_not_empty",
        body="defer",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule, finished_at) VALUES (?,?,?,?, 'refused', ?, ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                "composer_not_empty",
                "2020-01-01 00:00:00",
                decided_by_rule,
                "2020-01-01 00:00:01",
            ),
        )


def _notify_kw(thread: str) -> dict[str, str]:
    return dict(
        display="9",
        surface="grok",
        agent_id="grok-bot-gc1",
        thread_scope=thread,
    )


def test_notify_composer_empty_rearms_escalated_composer_defer(db: None) -> None:
    """Break: sweep refuse before empty notify leaves escalated row unclaimable."""
    detail = create_thread(thread_id=None, slug="arity-rearm-escalated")
    assert detail is not None
    thread = str(detail["id"])
    old_turn_id, _ = _seed_escalated_composer_defer(thread)
    kw = _notify_kw(thread)
    assert claim_due_wakes(now=_CLAIM_NOW)["wake_id"] is None
    assert notify_composer_empty_for_target(**kw) == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule, finished_at FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "pending"
    assert row["decided_by_rule"] is None
    assert row["finished_at"] is None
    claimed = claim_due_wakes(now=_CLAIM_NOW)
    assert claimed["wake_id"]
    claimed_turns = {
        int(r["turn_id"]) for r in claimed["rows"] if r.get("state") == "claimed"
    }
    assert old_turn_id in claimed_turns
    assert notify_composer_empty_for_target(**kw) == 0


@pytest.mark.parametrize(
    "decided_by_rule",
    [None, LEGACY_NORMALIZE_AGENT_MISMATCH],
)
def test_notify_composer_empty_skips_refused_composer_without_escalation_rule(
    db: None, decided_by_rule: str | None
) -> None:
    detail = create_thread(thread_id=None, slug="arity-skip-non-escalated")
    assert detail is not None
    thread = str(detail["id"])
    _seed_refused_composer_without_escalation(thread, decided_by_rule=decided_by_rule)
    assert notify_composer_empty_for_target(**_notify_kw(thread)) == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["decided_by_rule"] == decided_by_rule


def test_notify_composer_empty_skips_escalated_on_other_thread(db: None) -> None:
    a = create_thread(thread_id=None, slug="arity-other-a")
    b = create_thread(thread_id=None, slug="arity-other-b")
    assert a is not None and b is not None
    thread_a, thread_b = str(a["id"]), str(b["id"])
    _seed_escalated_composer_defer(thread_b)
    assert notify_composer_empty_for_target(**_notify_kw(thread_a)) == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, decided_by_rule FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread_b,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["decided_by_rule"] == COMPOSER_DEFER_ESCALATED


def test_notify_composer_empty_skips_escalated_rule_with_non_composer_reason(
    db: None,
) -> None:
    detail = create_thread(thread_id=None, slug="arity-non-composer-reason")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|agent",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "decided_by_rule, finished_at) VALUES (?,?,?,?, 'refused', ?, ?, ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                "agent_id_mismatch",
                "2020-01-01 00:00:00",
                COMPOSER_DEFER_ESCALATED,
                "2020-01-01 00:00:01",
            ),
        )
    assert notify_composer_empty_for_target(**_notify_kw(thread)) == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, decided_by_rule FROM endpoint_wake_ledger "
            "WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["reason"] == "agent_id_mismatch"
    assert row["decided_by_rule"] == COMPOSER_DEFER_ESCALATED


def test_notify_rearm_groups_with_newer_pending_on_same_thread(db: None) -> None:
    """Break: re-arm only the escalated row while a newer pending row already exists."""
    detail = create_thread(thread_id=None, slug="arity-group-newer-pending")
    assert detail is not None
    thread = str(detail["id"])
    old_turn_id, _ = _seed_escalated_composer_defer(thread)
    _seed_pending_composer_defer(
        thread, reason="composer_not_empty", decided_by_rule=None
    )
    with connect() as conn:
        newer_turn_id = conn.execute(
            "SELECT turn_id FROM endpoint_wake_ledger WHERE thread_id=? "
            "AND state='pending' ORDER BY turn_id DESC LIMIT 1",
            (thread,),
        ).fetchone()["turn_id"]
    assert notify_composer_empty_for_target(**_notify_kw(thread)) == 1
    first = claim_due_wakes(now=_CLAIM_NOW)
    assert first["wake_id"]
    claimed_ids = sorted(
        int(r["turn_id"]) for r in first["rows"] if r.get("state") == "claimed"
    )
    assert claimed_ids == sorted([old_turn_id, int(newer_turn_id)])
    assert claim_due_wakes(now=_CLAIM_NOW)["wake_id"] is None


def test_notify_rearm_with_newer_delivered_claims_only_rearmed_row(db: None) -> None:
    """Pin: delivered debounce does not block claiming the re-armed older turn alone."""
    detail = create_thread(thread_id=None, slug="arity-newer-delivered")
    assert detail is not None
    thread = str(detail["id"])
    old_turn_id, old_num = _seed_escalated_composer_defer(thread)
    new_turn_id, _ts, new_num, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|newer",
        body="newer",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, route, "
            "created_at, finished_at) VALUES (?,?,?,?, 'delivered', 'ok', 'exact', ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                new_turn_id,
                new_num,
                "2020-01-01 00:00:02",
                "2020-01-01 00:00:03",
            ),
        )
    assert notify_composer_empty_for_target(**_notify_kw(thread)) == 1
    claimed = claim_due_wakes(now=_CLAIM_NOW)
    assert claimed["wake_id"]
    claimed_turns = {
        int(r["turn_id"]) for r in claimed["rows"] if r.get("state") == "claimed"
    }
    assert claimed_turns == {old_turn_id}
    skipped = [r for r in claimed["rows"] if r.get("state") == "skipped"]
    assert not any(int(r["turn_id"]) == new_turn_id for r in skipped)


def test_notify_composer_empty_skips_title_mismatch_refused(db: None) -> None:
    """Break: broad refused match would resurrect terminal title_mismatch rows."""
    detail = create_thread(thread_id=None, slug="arity-skip-title-mismatch")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _ts, number, _c = insert_turn(
        thread=thread,
        from_agent="cursor",
        to_agent="grok-bot-gc1",
        subject="FACT|title",
        body="b",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, reason, created_at, "
            "finished_at) VALUES (?,?,?,?, 'refused', 'title_mismatch', ?, ?)",
            (
                "grok-bot-gc1",
                thread,
                turn_id,
                number,
                "2020-01-01 00:00:00",
                "2020-01-01 00:00:02",
            ),
        )
    cleared = notify_composer_empty_for_target(
        display="9",
        surface="grok",
        agent_id="grok-bot-gc1",
        thread_scope=thread,
    )
    assert cleared == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, finished_at FROM endpoint_wake_ledger WHERE thread_id=?",
            (thread,),
        ).fetchone()
    assert row["state"] == "refused"
    assert row["reason"] == "title_mismatch"
    assert row["finished_at"] == "2020-01-01 00:00:02"


def test_composer_defer_functions_reference_dynamic_in_sql() -> None:
    """Arity drift: SQL must be built via composer_defer_reason_in_sql(), not literals."""
    import bus_watch.endpoint_wake_composer_defer as mod

    for name in (
        "normalize_legacy_pending_wakes",
        "finalize_composer_defer_pass",
        "notify_composer_empty_for_target",
    ):
        source = inspect.getsource(getattr(mod, name))
        assert "composer_defer_reason_in_sql()" in source
        assert "IN (?,?,?)" not in source
