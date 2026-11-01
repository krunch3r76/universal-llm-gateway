"""Offline tests for work_registry_backfill eligibility, snapshot, and apply bounds."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "work_registry_backfill.py"


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location("work_registry_backfill", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _insert(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    status: str = "running",
    record_json: str = "{}",
    caller_agent: str | None = None,
    contract: str = "implement",
    park_kind: str | None = None,
    park_resumed_by: str | None = None,
    park_expires_at: str | None = None,
) -> None:
    stamp = _now_iso()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, resolved_model, message_present, "
            "status, record_json, contract, caller_agent, queued_at, started_at, "
            "park_kind, park_resumed_by, park_expires_at) "
            "VALUES (?, ?, '21650', 'cursor/test', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dispatch_id,
                dispatch_id,
                status,
                record_json,
                contract,
                caller_agent,
                stamp,
                stamp,
                park_kind,
                park_resumed_by,
                park_expires_at,
            ),
        )


@pytest.fixture()
def isolated_ledger():
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("DELETE FROM cursor_sdk_dispatches")
    yield ledger
    CursorDispatchLedger._instance = None


def test_terminal_completed_not_eligible(isolated_ledger: CursorDispatchLedger) -> None:
    mod = _load_backfill_module()
    _insert(
        isolated_ledger,
        dispatch_id="term-1",
        status="completed",
        record_json="{}",
    )
    rows = mod.fetch_rows(isolated_ledger)
    assert mod.collect_targets(rows, only_ids=None) == []


def test_running_incomplete_is_target(isolated_ledger: CursorDispatchLedger) -> None:
    mod = _load_backfill_module()
    _insert(isolated_ledger, dispatch_id="run-1", status="running")
    targets = mod.collect_targets(mod.fetch_rows(isolated_ledger), only_ids=None)
    assert [r["dispatch_id"] for r in targets] == ["run-1"]


def test_open_park_for_restart_cancelled_is_target(
    isolated_ledger: CursorDispatchLedger,
) -> None:
    mod = _load_backfill_module()
    _insert(
        isolated_ledger,
        dispatch_id="park-1",
        status="cancelled",
        park_kind="park_for_restart",
        park_expires_at=None,
    )
    targets = mod.collect_targets(mod.fetch_rows(isolated_ledger), only_ids=None)
    assert [r["dispatch_id"] for r in targets] == ["park-1"]


def test_closed_park_cancelled_not_target(isolated_ledger: CursorDispatchLedger) -> None:
    mod = _load_backfill_module()
    _insert(
        isolated_ledger,
        dispatch_id="park-done",
        status="cancelled",
        park_kind="park_for_restart",
        park_resumed_by="child-1",
    )
    assert mod.collect_targets(mod.fetch_rows(isolated_ledger), only_ids=None) == []


def test_apply_only_touches_allowed_fields(isolated_ledger: CursorDispatchLedger) -> None:
    mod = _load_backfill_module()
    rec = {"custom_flag": True, "lane_worktree": "/tmp/lane-21650"}
    _insert(
        isolated_ledger,
        dispatch_id="apply-1",
        status="admitted",
        record_json=json.dumps(rec),
        caller_agent=None,
    )
    rows = mod.fetch_rows(isolated_ledger)
    mod.apply_backfill_row(isolated_ledger, rows[0])
    with isolated_ledger._connect() as conn:
        row = conn.execute(
            "SELECT caller_agent, record_json FROM cursor_sdk_dispatches "
            "WHERE dispatch_id='apply-1'"
        ).fetchone()
    assert row["caller_agent"] == "cursor-sdk"
    after = json.loads(row["record_json"])
    assert after["custom_flag"] is True
    assert after["seat_name"]
    assert after["address"]["kind"] == "cursor_sdk"
    mod.assert_record_json_backfill_only(rec, after)


def test_snapshot_and_limit_apply(isolated_ledger: CursorDispatchLedger, tmp_path: Path) -> None:
    mod = _load_backfill_module()
    for i in range(3):
        _insert(isolated_ledger, dispatch_id=f"lim-{i}", status="queued")
    snap = tmp_path / "snap.json"
    stats = mod.run_backfill(
        apply=True,
        limit=2,
        only_ids=None,
        snapshot_path=snap,
        ledger=isolated_ledger,
    )
    assert stats["targeted"] == 3
    assert stats["updated"] == 2
    assert len(stats["applied_ids"]) == 2
    body = json.loads(snap.read_text(encoding="utf-8"))
    assert body["count"] == 3
    assert len(body["rows"]) == 3
