from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from systems.pipeline.core.execution.dispatch_journal import (
    fetch_record,
    initialize_schema,
    journal_terminal,
    sweep_orphan_started,
)
from systems.pipeline.core.execution.dispatch_journal_transitions import (
    sweep_orphan_started_sync,
    write_transition_sync,
)
from systems.pipeline.core.execution.test_dispatch_journal import (
    _iso,
    _make_terminal_record,
)


def _insert_started(
    path,
    execution_id: str,
    *,
    started_at: str,
    pipeline: str = "frontier-dispatch",
) -> None:
    write_transition_sync(
        path,
        execution_id=execution_id,
        pipeline=pipeline,
        status="started",
        caller_agent=None,
        started_at=started_at,
        completed_at=None,
        record_json={
            "execution_id": execution_id,
            "pipeline": pipeline,
            "status": "started",
            "started_at": started_at,
        },
    )


@pytest.mark.asyncio
async def test_orphan_sweep_marks_pre_boot_started_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()
    from systems.pipeline.core.execution.dispatch_journal import _journal_path

    old_started = _iso(datetime.now(UTC) - timedelta(hours=1))
    _insert_started(_journal_path(), "exec-orphan", started_at=old_started)

    boot_ts = time.time()
    count = await sweep_orphan_started(boot_ts)
    assert count == 1

    fetched = await fetch_record("exec-orphan")
    assert fetched is not None
    assert fetched["status"] == "failed"
    assert fetched["error"]["code"] == "interrupted_by_restart"
    assert fetched["error"]["data"]["resumable"] is True
    assert fetched["as_of"] == fetched["completed_at"]


@pytest.mark.asyncio
async def test_orphan_sweep_emits_interrupted_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()
    from systems.pipeline.core.execution.dispatch_journal import _journal_path

    old_started = _iso(datetime.now(UTC) - timedelta(hours=1))
    _insert_started(_journal_path(), "exec-event", started_at=old_started)

    events: list[object] = []

    class _FakeBus:
        async def publish_nowait(self, event: object) -> None:
            events.append(event)

    boot_ts = time.time()
    count = await sweep_orphan_started(boot_ts, event_bus=_FakeBus())
    await asyncio.sleep(0)
    assert count == 1
    assert len(events) == 1
    event = events[0]
    assert getattr(event, "signal") == "pipeline.dispatch.interrupted"
    payload = getattr(event, "payload")
    assert payload["execution_id"] == "exec-event"
    assert payload["pipeline"] == "frontier-dispatch"


@pytest.mark.asyncio
async def test_orphan_sweep_leaves_completed_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    completed_at = _iso(datetime.now(UTC) - timedelta(hours=2))
    await journal_terminal(
        _make_terminal_record("exec-done", completed_at=completed_at)
    )

    count = await sweep_orphan_started(time.time())
    assert count == 0

    fetched = await fetch_record("exec-done")
    assert fetched is not None
    assert fetched["status"] == "completed"


@pytest.mark.asyncio
async def test_orphan_sweep_leaves_post_boot_started_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()
    from systems.pipeline.core.execution.dispatch_journal import _journal_path

    # Process boot predates this dispatch; avoid same-second float vs ISO skew.
    boot_ts = time.time() - 60.0
    fresh_started = _iso(datetime.now(UTC))
    _insert_started(_journal_path(), "exec-fresh", started_at=fresh_started)

    count = await sweep_orphan_started(boot_ts)
    assert count == 0

    fetched = await fetch_record("exec-fresh")
    assert fetched is not None
    assert fetched["status"] == "started"


@pytest.mark.asyncio
async def test_orphan_sweep_second_pass_updates_zero_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()
    from systems.pipeline.core.execution.dispatch_journal import _journal_path

    old_started = _iso(datetime.now(UTC) - timedelta(hours=1))
    _insert_started(_journal_path(), "exec-once", started_at=old_started)

    boot_ts = time.time()
    assert await sweep_orphan_started(boot_ts) == 1
    assert await sweep_orphan_started(boot_ts) == 0


def test_orphan_sweep_sync_cas_skips_completed_race(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare-and-set: concurrent terminal flip between read and UPDATE is not swept."""
    path = tmp_path / "pipeline-dispatch.db"
    from systems.pipeline.core.execution import dispatch_journal_transitions as djt

    old_started = _iso(datetime.now(UTC) - timedelta(hours=1))
    _insert_started(path, "exec-race", started_at=old_started)

    real_iso_epoch = djt._iso_epoch
    flipped = {"done": False}

    def _racing_iso_epoch(iso_ts: str) -> float:
        val = real_iso_epoch(iso_ts)
        if not flipped["done"]:
            flipped["done"] = True
            with sqlite3.connect(path) as connection:
                djt.migrate_schema_sync(connection)
                connection.execute(
                    """
                    UPDATE dispatch_records SET status = 'completed'
                    WHERE execution_id = ?
                    """,
                    ("exec-race",),
                )
                connection.commit()
        return val

    monkeypatch.setattr(djt, "_iso_epoch", _racing_iso_epoch)
    updated = sweep_orphan_started_sync(path, process_started_at=time.time())
    assert updated == []
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status FROM dispatch_records WHERE execution_id = ?",
            ("exec-race",),
        ).fetchone()
    assert row is not None
    assert row[0] == "completed"
