"""Track 4: faster dead-run reaping + lease_snapshot robustness."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger


class _LiveTask:
    def done(self) -> bool:
        return False


@pytest.fixture()
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def _insert_active_writer(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    worker_instance: str,
    last_heartbeat_at: str | None,
    started_at: str | None = None,
    source_repo: str = "/mnt/torus/projects/universal-llm-gateway",
) -> None:
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only, "
            " worker_instance, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                "fp",
                "2680",
                "exec-1",
                "cursor/composer-2.5",
                0,
                "running",
                json.dumps({"model": "cursor/composer-2.5"}),
                source_repo,
                0,
                worker_instance,
                started_at or last_heartbeat_at,
                last_heartbeat_at,
            ),
        )


def test_stale_writers_reaps_dead_run_after_short_grace(
    ledger: CursorDispatchLedger,
) -> None:
    worker = "live-worker"
    old_hb = (datetime.now(UTC) - timedelta(seconds=90)).isoformat()
    _insert_active_writer(
        ledger,
        dispatch_id="dead-run-1",
        worker_instance=worker,
        last_heartbeat_at=old_hb,
    )

    stale = ledger.stale_writers(
        threshold_s=1980.0,
        dead_run_grace_s=60.0,
        worker_instance=worker,
    )

    assert stale == ["dead-run-1"]


def test_stale_writers_keeps_live_task_until_long_threshold(
    ledger: CursorDispatchLedger,
) -> None:
    worker = "live-worker"
    old_hb = (datetime.now(UTC) - timedelta(seconds=90)).isoformat()
    _insert_active_writer(
        ledger,
        dispatch_id="live-run-1",
        worker_instance=worker,
        last_heartbeat_at=old_hb,
    )
    ledger.register_task("live-run-1", _LiveTask())  # type: ignore[arg-type]

    stale = ledger.stale_writers(
        threshold_s=1980.0,
        dead_run_grace_s=60.0,
        worker_instance=worker,
    )

    assert stale == []


def test_stale_writers_reaps_live_task_past_long_threshold(
    ledger: CursorDispatchLedger,
) -> None:
    worker = "live-worker"
    old_hb = (datetime.now(UTC) - timedelta(seconds=2000)).isoformat()
    _insert_active_writer(
        ledger,
        dispatch_id="stale-live-1",
        worker_instance=worker,
        last_heartbeat_at=old_hb,
    )
    ledger.register_task("stale-live-1", _LiveTask())  # type: ignore[arg-type]

    stale = ledger.stale_writers(
        threshold_s=1980.0,
        dead_run_grace_s=60.0,
        worker_instance=worker,
    )

    assert stale == ["stale-live-1"]


def test_lease_snapshot_without_source_repo_when_holder_exists(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_active_writer(
        ledger,
        dispatch_id="holder-1",
        worker_instance="worker-a",
        last_heartbeat_at=datetime.now(UTC).isoformat(),
        source_repo=repo,
    )

    snap = ledger.lease_snapshot()

    assert snap["holder_dispatch_id"] == "holder-1"
    assert snap["holder_status"] == "running"
    assert snap["holder_source_repo"] == repo
    assert snap["queue_depth"] == 0


def test_lease_snapshot_with_source_repo_filter(
    ledger: CursorDispatchLedger,
) -> None:
    repo = "/mnt/torus/projects/universal-llm-gateway"
    _insert_active_writer(
        ledger,
        dispatch_id="holder-repo",
        worker_instance="worker-a",
        last_heartbeat_at=datetime.now(UTC).isoformat(),
        source_repo=repo,
    )

    snap = ledger.lease_snapshot(source_repo=repo)

    assert snap["holder_dispatch_id"] == "holder-repo"
    assert snap["holder_source_repo"] == repo
