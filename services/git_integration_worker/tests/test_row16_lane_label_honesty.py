"""Row-16: lane label honesty for nest-inherited dispatches on shared master."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    resolve_admit_lane,
    resolve_isolation_materialized,
)
from services.git_integration_worker.cursor_sdk_concurrency_meter import (
    ambient_cause_filtered_stats,
)
from services.git_integration_worker.cursor_sdk_concurrency_posture import (
    reported_admit_lane,
    stamp_isolation_on_record_json,
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    STRUCTURED_CLOSEOUT_FULL_HEADING,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield tmp_path
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-row16",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-row16",
        "execution_id": "exec-disp-row16",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _write_closeout(closeout_root: Path, dispatch_id: str, ambient: bool) -> None:
    payload = {
        "schema_version": 1,
        "status": "complete",
        "dispatch_id": dispatch_id,
        "files_ambient_repo_movement": (
            [{"path": "x.py", "cause": "ambient:concurrent_edit"}] if ambient else []
        ),
    }
    closeout_root.mkdir(parents=True, exist_ok=True)
    (closeout_root / f"{dispatch_id}.md").write_text(
        f"closeout\n\n{STRUCTURED_CLOSEOUT_FULL_HEADING}\n\n"
        f"{json.dumps(payload, indent=2)}\n",
        encoding="utf-8",
    )


def test_reported_admit_lane_nest_on_shared_master_is_a(tmp_path: Path) -> None:
    repo = str(tmp_path / "repo")
    Path(repo).mkdir()
    wt = tmp_path / "worktrees" / "d1"
    wt.mkdir(parents=True)
    assert (
        reported_admit_lane(
            selected_lane="B",
            lease_key=repo,
            source_repo=repo,
        )
        == "A"
    )
    assert (
        reported_admit_lane(
            selected_lane="B",
            lease_key=str(wt),
            source_repo=repo,
        )
        == "B"
    )


def test_admit_stamps_isolation_materialized_on_record_json() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"
    req = _req(dispatch_id="nest-shared", execution_id="exec-nest-shared", lane="A")
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="nest-shared",
            thread_id="t-row16",
            model_id="composer-2.5",
        ),
        contract="implement",
        source_repo=repo,
        lease_key=repo,
        concurrency_posture="nest_child",
        write_lease_slot_limit=1,
        isolation_materialized=False,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("nest-shared",),
        ).fetchone()
    assert row is not None
    data = json.loads(row["record_json"])
    assert data["lane"] == "A"
    assert data["isolation_materialized"] is False


def test_resolve_isolation_materialized_reclassifies_historical_false_b() -> None:
    stale = stamp_isolation_on_record_json('{"lane":"B"}', isolation_materialized=False)
    assert (
        resolve_isolation_materialized(
            record_json=stale,
            lease_key="/repo",
            source_repo="/repo",
        )
        is False
    )
    assert (
        resolve_admit_lane(
            record_json=stale,
            lease_key="/repo",
            source_repo="/repo",
        )
        == "A"
    )


def test_ambient_rate_by_lane_b_counts_materialized_only(tmp_path: Path) -> None:
    """AC2/AC3: 15 materialized-B + 6 stale nominal-B → B ambient 1/15 not 2/21."""
    source_repo = str(tmp_path / "repo")
    closeout_root = Path(source_repo) / "tmp" / "reviews" / "closeouts"
    post_floor_terminal = "2026-08-02T12:00:00+00:00"
    ledger = CursorDispatchLedger.instance()

    rows: list[dict] = []
    for index in range(15):
        dispatch_id = f"mat-b-{index:02d}"
        wt = str(tmp_path / "worktrees" / dispatch_id)
        Path(wt).mkdir(parents=True, exist_ok=True)
        record_json = json.dumps(
            {
                "lane": "B",
                "isolation_materialized": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        rows.append(
            {
                "dispatch_id": dispatch_id,
                "contract": "implement",
                "read_only": 0,
                "started_at": "2026-08-02T10:00:00+00:00",
                "terminal_at": post_floor_terminal,
                "record_json": record_json,
                "lease_key": wt,
                "source_repo": source_repo,
            }
        )
        _write_closeout(closeout_root, dispatch_id, ambient=(index == 0))

    for index in range(6):
        dispatch_id = f"nom-b-{index}"
        stale = json.dumps({"lane": "B"}, sort_keys=True, separators=(",", ":"))
        rows.append(
            {
                "dispatch_id": dispatch_id,
                "contract": "implement",
                "read_only": 0,
                "started_at": "2026-08-02T10:00:00+00:00",
                "terminal_at": post_floor_terminal,
                "record_json": stale,
                "lease_key": source_repo,
                "source_repo": source_repo,
            }
        )
        _write_closeout(closeout_root, dispatch_id, ambient=True)

    stats = ambient_cause_filtered_stats(
        ledger_rows=rows,
        closeout_root=closeout_root,
        lane_filter="B",
    )
    assert stats["post_floor_rate"] == "1/15"

    stats_a = ambient_cause_filtered_stats(
        ledger_rows=rows,
        closeout_root=closeout_root,
        lane_filter="A",
    )
    assert stats_a["post_floor_rate"] == "6/6"
