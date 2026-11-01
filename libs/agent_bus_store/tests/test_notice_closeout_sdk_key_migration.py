"""Migration backfill keys must match runtime sdk CLOSEOUT parsing."""

from __future__ import annotations

import sqlite3

import pytest
from agent_bus_store.db.migrations.migration_024 import run as migration_024_run
from agent_bus_store.notice_closeout_dedupe import notice_closeout_key_for_send
from agent_bus_store.notice_closeout_sdk_key import sdk_notice_closeout_key

pytestmark = pytest.mark.offline


def _minimal_turns_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE turns ("
        "id INTEGER PRIMARY KEY, thread TEXT, from_agent TEXT, subject TEXT, "
        "notice_closeout_key TEXT)"
    )
    return conn


def test_migration_and_runtime_key_match_pipe_in_dispatch_id() -> None:
    subject = "cursor-sdk CLOSEOUT abc|x extra | giw pointer"
    runtime = notice_closeout_key_for_send(
        from_agent="cursor-sdk", subject=subject, body=""
    )
    assert runtime == "sdk:abc|x"
    conn = _minimal_turns_db()
    conn.execute(
        "INSERT INTO turns (thread, from_agent, subject) VALUES (?,?,?)",
        ("1", "cursor-sdk", subject),
    )
    migration_024_run(conn)
    row = conn.execute(
        "SELECT notice_closeout_key FROM turns WHERE id=1"
    ).fetchone()
    assert row is not None
    assert row["notice_closeout_key"] == runtime
    assert sdk_notice_closeout_key(from_agent="cursor-sdk", subject=subject) == runtime
