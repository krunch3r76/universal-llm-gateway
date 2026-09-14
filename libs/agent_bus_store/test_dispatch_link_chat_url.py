"""Dispatch-link chat_url columns and the seating-time projection."""

from __future__ import annotations

import pytest

from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.migrations import run_migrations
from agent_bus_store.db.threads_atomic import update_dispatch_link_chat_url


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def test_migrations_add_chat_url_columns(bus_db) -> None:
    """The columns are asserted by name, not by the migration that added them.

    chat_url arrived in migration_012; an earlier draft of this test pinned it
    to migration_010, which now carries resume_fence_events instead.
    """
    with connect() as conn:
        run_migrations(conn)
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(thread_dispatch_links)").fetchall()
        }
    assert "chat_url" in cols
    assert "chat_url_bound_at" in cols


def test_update_dispatch_link_chat_url_at_seating(bus_db) -> None:
    """AC4: seating callback projection writes chat_url by execution_id."""
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-url-test",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp admit",
        body="body",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    execution_id = "exec-seat-1"
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="team-dispatch",
        caller_agent="dispatch",
    )
    updated = update_dispatch_link_chat_url(
        thread_id=thread_id,
        execution_id=execution_id,
        chat_url="https://claude.ai/cowork/cse_proj",
        now_ts="2026-09-07T20:00:00Z",
    )
    assert updated == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT chat_url, chat_url_bound_at FROM thread_dispatch_links "
            "WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    assert row["chat_url"] == "https://claude.ai/cowork/cse_proj"
    assert row["chat_url_bound_at"] == "2026-09-07T20:00:00Z"


def test_chat_url_projection_is_keyed_by_thread_not_execution_alone(bus_db) -> None:
    """A matching execution_id under the wrong thread must not be written.

    The key is ``(thread_id, execution_id)``; keying on execution_id alone
    would let one thread's seating callback overwrite another's link row.
    """
    thread_row, *_ = create_thread_with_turn(
        slug="cdp-url-key",
        from_agent="dispatch",
        to_agent="web-anthropic",
        subject="cdp admit",
        body="body",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    execution_id = "exec-seat-2"
    admit_dispatch(
        thread_id=thread_id,
        execution_id=execution_id,
        pipeline_id="team-dispatch",
        caller_agent="dispatch",
    )
    updated = update_dispatch_link_chat_url(
        thread_id=str(int(thread_id) + 1),
        execution_id=execution_id,
        chat_url="https://claude.ai/cowork/wrong",
    )
    assert updated == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT chat_url FROM thread_dispatch_links WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
    assert row["chat_url"] is None
