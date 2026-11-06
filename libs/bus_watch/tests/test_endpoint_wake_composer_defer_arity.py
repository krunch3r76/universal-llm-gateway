"""Composer-defer SQL placeholder arity vs COMPOSER_DEFER_REASONS."""

from __future__ import annotations

import inspect

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from bus_watch.endpoint_wake_composer_defer import (
    COMPOSER_DEFER_ARMED,
    COMPOSER_DEFER_REASONS,
    COMPOSER_DEFER_SWEEP,
    composer_defer_reason_in_sql,
    composer_defer_reasons_sorted,
    finalize_composer_defer_pass,
    normalize_legacy_pending_wakes,
    notify_composer_empty_for_target,
)

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
