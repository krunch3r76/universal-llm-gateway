"""Hermetic tests for cursor-sdk concurrency meter (P2 Leg 1)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_concurrency_meter import (
    ATTRIBUTION_FLOOR_ISO,
    DispatchInterval,
    active_work_lane_fields,
    ambient_cause_filtered_stats,
    concurrency_stats,
    count_overlap_pairs,
    peak_concurrent,
    peak_concurrent_for_lane,
    resolve_concurrency_stats_window,
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
        "thread_id": "t-meter",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-meter",
        "execution_id": "exec-disp-meter",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_and_terminal(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    contract: str | None,
    read_only: bool,
    started_at: str,
    terminal_at: str,
    source_repo: str,
    lane: str | None = None,
    lease_key: str | None = None,
) -> None:
    req = _req(
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        handoff_contract=contract,
        read_only=read_only,
        lane=lane,
    )
    effective_lease = lease_key or source_repo
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        contract=contract,
        source_repo=source_repo,
        lease_key=effective_lease,
        read_only=read_only,
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET started_at=?, terminal_at=? WHERE dispatch_id=?",
            (started_at, terminal_at, dispatch_id),
        )
        conn.commit()


def _closeout_body(
    *,
    dispatch_id: str,
    ambient: list[dict[str, str]] | None = None,
    schema_version: int = 1,
    omit_key: bool = False,
) -> str:
    payload: dict[str, object] = {
        "schema_version": schema_version,
        "status": "complete",
        "dispatch_id": dispatch_id,
    }
    if not omit_key:
        payload["files_ambient_repo_movement"] = ambient or []
    return json.dumps(payload, indent=2)


def _write_closeout(closeout_root: Path, dispatch_id: str, body: str) -> None:
    closeout_root.mkdir(parents=True, exist_ok=True)
    path = closeout_root / f"{dispatch_id}.md"
    path.write_text(
        f"closeout\n\n{STRUCTURED_CLOSEOUT_FULL_HEADING}\n\n{body}\n",
        encoding="utf-8",
    )


def _freeze_meter_now(monkeypatch: pytest.MonkeyPatch, iso: str) -> None:
    fixed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_concurrency_meter._utc_now",
        lambda: fixed,
    )


def test_resolve_concurrency_stats_window_default_retention() -> None:
    start, end = resolve_concurrency_stats_window(
        window_end="2026-08-15T12:00:00+00:00",
    )
    assert end == "2026-08-15T12:00:00+00:00"
    assert start == "2026-08-01T12:00:00+00:00"


def test_resolve_concurrency_stats_window_preserves_explicit_bounds() -> None:
    start, end = resolve_concurrency_stats_window(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-03T00:00:00+00:00",
    )
    assert start == "2026-08-01T00:00:00+00:00"
    assert end == "2026-08-03T00:00:00+00:00"


def test_default_retention_excludes_old_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default rolling window excludes ledger rows older than retention days."""
    source_repo = str(tmp_path / "repo")
    closeout_root = Path(source_repo) / "tmp" / "reviews" / "closeouts"
    closeout_root.mkdir(parents=True, exist_ok=True)
    ledger = CursorDispatchLedger.instance()
    _freeze_meter_now(monkeypatch, "2026-08-15T12:00:00+00:00")
    _admit_and_terminal(
        ledger,
        dispatch_id="recent",
        contract="implement",
        read_only=False,
        started_at="2026-08-14T10:00:00+00:00",
        terminal_at="2026-08-14T10:05:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="stale",
        contract="implement",
        read_only=False,
        started_at="2026-07-01T10:00:00+00:00",
        terminal_at="2026-07-01T10:05:00+00:00",
        source_repo=source_repo,
    )
    meter = concurrency_stats(ledger=ledger, closeout_root=closeout_root)
    assert meter["window_start"] == "2026-08-01T12:00:00+00:00"
    assert meter["window_end"] == "2026-08-15T12:00:00+00:00"
    rows = ledger.interval_rows_in_window(
        window_start=meter["window_start"],
        window_end=meter["window_end"],
    )
    assert {row["dispatch_id"] for row in rows} == {"recent"}


def test_explicit_window_selection_unchanged(tmp_path: Path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="inside",
        contract="implement",
        read_only=False,
        started_at="2026-08-02T10:00:00+00:00",
        terminal_at="2026-08-02T10:05:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="outside",
        contract="implement",
        read_only=False,
        started_at="2026-07-01T10:00:00+00:00",
        terminal_at="2026-07-01T10:05:00+00:00",
        source_repo=source_repo,
    )
    closeout_root = Path(source_repo) / "tmp" / "reviews" / "closeouts"
    closeout_root.mkdir(parents=True, exist_ok=True)
    meter = concurrency_stats(
        ledger=ledger,
        closeout_root=closeout_root,
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-03T00:00:00+00:00",
    )
    assert meter["window_start"] == "2026-08-01T00:00:00+00:00"
    assert meter["window_end"] == "2026-08-03T00:00:00+00:00"
    rows = ledger.interval_rows_in_window(
        window_start=meter["window_start"],
        window_end=meter["window_end"],
    )
    assert {row["dispatch_id"] for row in rows} == {"inside"}


def test_peak_concurrent_write_implement_three_overlaps() -> None:
    """AC1: three synthetic overlapping write intervals → peak=3."""
    intervals = [
        DispatchInterval(
            "a",
            "implement",
            False,
            "2026-08-01T10:00:00+00:00",
            "2026-08-01T10:30:00+00:00",
        ),
        DispatchInterval(
            "b",
            "light-bounded",
            False,
            "2026-08-01T10:10:00+00:00",
            "2026-08-01T10:25:00+00:00",
        ),
        DispatchInterval(
            "c",
            "implement",
            False,
            "2026-08-01T10:15:00+00:00",
            "2026-08-01T10:20:00+00:00",
        ),
    ]
    assert peak_concurrent(intervals, write_only=True) == 3


def test_ambient_cause_filtered_includes_censoring_triple(tmp_path: Path) -> None:
    """AC2: ambient stats expose n_parseable / n_unparseable / n_key_absent."""
    closeout_root = tmp_path / "closeouts"
    post_floor_terminal = "2026-08-01T23:00:00+00:00"
    rows = [
        {
            "dispatch_id": "parse-ok",
            "contract": "implement",
            "read_only": 0,
            "started_at": "2026-08-01T22:30:00+00:00",
            "terminal_at": post_floor_terminal,
        },
        {
            "dispatch_id": "parse-bad",
            "contract": "implement",
            "read_only": 0,
            "started_at": "2026-08-01T22:30:00+00:00",
            "terminal_at": post_floor_terminal,
        },
        {
            "dispatch_id": "no-key",
            "contract": "implement",
            "read_only": 0,
            "started_at": "2026-08-01T22:30:00+00:00",
            "terminal_at": post_floor_terminal,
        },
    ]
    _write_closeout(
        closeout_root,
        "parse-ok",
        _closeout_body(
            dispatch_id="parse-ok",
            ambient=[{"path": "a.py", "cause": "ambient:concurrent_edit"}],
        ),
    )
    _write_closeout(
        closeout_root,
        "parse-bad",
        _closeout_body(dispatch_id="wrong-id", ambient=[]),
    )
    closeout_root.mkdir(parents=True, exist_ok=True)
    (closeout_root / "no-key.md").write_text("prose only\n", encoding="utf-8")

    stats = ambient_cause_filtered_stats(
        ledger_rows=rows,
        closeout_root=closeout_root,
        floor_iso=ATTRIBUTION_FLOOR_ISO,
    )
    assert stats["n_parseable"] == 1
    assert stats["n_unparseable"] == 1
    assert stats["n_key_absent"] == 1
    assert stats["post_floor_n_parseable"] == 1
    assert stats["post_floor_ambient_star"] == 1
    assert stats["post_floor_rate"] == "1/1"


def test_census_fixture_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: fixture corpus reproduces peak=2, write_overlap_pairs=3, ambient:* 2/66."""
    source_repo = str(tmp_path / "repo")
    closeout_root = Path(source_repo) / "tmp" / "reviews" / "closeouts"
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", source_repo)
    _freeze_meter_now(monkeypatch, "2026-08-03T00:00:00+00:00")
    ledger = CursorDispatchLedger.instance()

    # Three non-overlapping write-implement pair windows → peak=2, pairs=3.
    pairs = [
        (
            (
                "census-a1",
                "light-bounded",
                "2026-08-01T13:09:00+00:00",
                "2026-08-01T13:14:00+00:00",
            ),
            (
                "census-b1",
                "implement",
                "2026-08-01T13:11:00+00:00",
                "2026-08-01T13:13:00+00:00",
            ),
        ),
        (
            (
                "census-a2",
                "light-bounded",
                "2026-08-01T22:13:00+00:00",
                "2026-08-01T22:16:00+00:00",
            ),
            (
                "census-b2",
                "implement",
                "2026-08-01T22:13:55+00:00",
                "2026-08-01T22:15:00+00:00",
            ),
        ),
        (
            (
                "census-a3",
                "light-bounded",
                "2026-08-01T22:27:00+00:00",
                "2026-08-01T22:32:00+00:00",
            ),
            (
                "census-b3",
                "implement",
                "2026-08-01T22:27:53+00:00",
                "2026-08-01T22:29:00+00:00",
            ),
        ),
    ]
    for left, right in pairs:
        for dispatch_id, contract, started_at, terminal_at in (left, right):
            _admit_and_terminal(
                ledger,
                dispatch_id=dispatch_id,
                contract=contract,
                read_only=False,
                started_at=started_at,
                terminal_at=terminal_at,
                source_repo=source_repo,
            )

    ambient_star_ids = {"census-pf-00", "census-pf-01"}
    for index in range(66):
        dispatch_id = f"census-pf-{index:02d}"
        hour = 10 + index // 60
        minute = index % 60
        started_at = f"2026-08-02T{hour:02d}:{minute:02d}:00+00:00"
        terminal_at = f"2026-08-02T{hour:02d}:{minute:02d}:30+00:00"
        _admit_and_terminal(
            ledger,
            dispatch_id=dispatch_id,
            contract="implement",
            read_only=False,
            started_at=started_at,
            terminal_at=terminal_at,
            source_repo=source_repo,
        )
        ambient = []
        if dispatch_id in ambient_star_ids:
            ambient = [{"path": "x.py", "cause": "ambient:concurrent_edit"}]
        _write_closeout(
            closeout_root,
            dispatch_id,
            _closeout_body(dispatch_id=dispatch_id, ambient=ambient),
        )

    meter = concurrency_stats(ledger=ledger, closeout_root=closeout_root)
    assert meter["peak_concurrent_write_implement"] == 2
    assert meter["write_overlap_pairs"] == 3
    ambient = meter["ambient_cause_filtered"]
    assert ambient["post_floor_n_parseable"] == 66
    assert ambient["post_floor_ambient_star"] == 2
    assert ambient["post_floor_rate"] == "2/66"


def test_concurrency_stats_route_and_active_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: /concurrency-stats exposes telemetry; /active-work omits historical census."""
    source_repo = tmp_path / "repo"
    closeout_root = source_repo / "tmp" / "reviews" / "closeouts"
    closeout_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(source_repo))
    _freeze_meter_now(monkeypatch, "2026-08-15T12:00:00+00:00")

    app = create_app()
    client = TestClient(app)

    stats_resp = client.get("/api/v1/cursor/concurrency-stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert "peak_concurrent_write_implement" in stats
    assert "ambient_cause_filtered" in stats
    assert stats["window_start"] is not None
    assert stats["window_end"] is not None
    assert {"n_parseable", "n_unparseable", "n_key_absent"} <= stats[
        "ambient_cause_filtered"
    ].keys()

    active_resp = client.get("/api/v1/git/active-work")
    assert active_resp.status_code == 200
    active = active_resp.json()
    assert "concurrency_stats" not in active
    assert "active_count" in active
    assert "lane_b" in active
    assert "write_lease" in active


def test_ledger_interval_window_filter(tmp_path: Path) -> None:
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    _admit_and_terminal(
        ledger,
        dispatch_id="inside",
        contract="implement",
        read_only=False,
        started_at="2026-08-02T10:00:00+00:00",
        terminal_at="2026-08-02T10:05:00+00:00",
        source_repo=source_repo,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="outside",
        contract="implement",
        read_only=False,
        started_at="2026-07-01T10:00:00+00:00",
        terminal_at="2026-07-01T10:05:00+00:00",
        source_repo=source_repo,
    )
    rows = ledger.interval_rows_in_window(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-03T00:00:00+00:00",
    )
    assert {row["dispatch_id"] for row in rows} == {"inside"}


def test_count_overlap_pairs_write_only() -> None:
    intervals = [
        DispatchInterval(
            "ro",
            "implement",
            True,
            "2026-08-01T10:00:00+00:00",
            "2026-08-01T10:30:00+00:00",
        ),
        DispatchInterval(
            "w1",
            "implement",
            False,
            "2026-08-01T10:05:00+00:00",
            "2026-08-01T10:15:00+00:00",
        ),
        DispatchInterval(
            "w2",
            "light-bounded",
            False,
            "2026-08-01T10:10:00+00:00",
            "2026-08-01T10:20:00+00:00",
        ),
    ]
    assert count_overlap_pairs(intervals, write_only=True) == 1
    assert count_overlap_pairs(intervals, write_only=False) == 3


def test_peak_concurrent_write_implement_by_lane_separates_b(tmp_path: Path) -> None:
    """AC-S7.3: overlapping Lane-B writers raise B peak; A peak stays at zero."""
    source_repo = str(tmp_path / "repo")
    ledger = CursorDispatchLedger.instance()
    lane_b_a = str(tmp_path / "wt-a")
    lane_b_b = str(tmp_path / "wt-b")
    _admit_and_terminal(
        ledger,
        dispatch_id="b-one",
        contract="implement",
        read_only=False,
        started_at="2026-08-02T10:00:00+00:00",
        terminal_at="2026-08-02T10:30:00+00:00",
        source_repo=source_repo,
        lane="B",
        lease_key=lane_b_a,
    )
    _admit_and_terminal(
        ledger,
        dispatch_id="b-two",
        contract="implement",
        read_only=False,
        started_at="2026-08-02T10:10:00+00:00",
        terminal_at="2026-08-02T10:20:00+00:00",
        source_repo=source_repo,
        lane="B",
        lease_key=lane_b_b,
    )
    rows = ledger.interval_rows_in_window()
    intervals = [
        DispatchInterval(
            dispatch_id=row["dispatch_id"],
            contract=row.get("contract"),
            read_only=bool(row.get("read_only")),
            started_at=str(row["started_at"]),
            terminal_at=str(row["terminal_at"]),
            lane="B",
        )
        for row in rows
    ]
    assert peak_concurrent_for_lane(intervals, write_only=True, lane="B") == 2
    assert peak_concurrent_for_lane(intervals, write_only=True, lane="A") == 0


def test_concurrency_stats_exposes_lane_dimension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S7.2/S7.4: census preserved and lane_b fields are present on routes."""
    source_repo = tmp_path / "repo"
    closeout_root = source_repo / "tmp" / "reviews" / "closeouts"
    closeout_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(source_repo))
    _freeze_meter_now(monkeypatch, "2026-08-15T12:00:00+00:00")

    meter = concurrency_stats(closeout_root=closeout_root, source_repo=source_repo)
    assert "peak_concurrent_write_implement_by_lane" in meter
    assert meter["peak_concurrent_write_implement_by_lane"] == {"A": 0, "B": 0}
    assert "ambient_rate_by_lane" in meter
    assert "lane_b_worktrees_live" in meter
    assert "lane_b_branches_unlanded" in meter
    assert meter["window_start"] is not None
    assert meter["window_end"] is not None

    lane_fields = active_work_lane_fields(source_repo=source_repo)
    assert "lane_b_regime" in lane_fields
    assert lane_fields["lane_b"]["worktrees_live"] is not None
    assert lane_fields["lane_b"]["branches_unlanded"] is not None

    app = create_app()
    client = TestClient(app)
    active_resp = client.get("/api/v1/git/active-work")
    assert active_resp.status_code == 200
    active = active_resp.json()
    assert "lane_b_regime" in active
    assert active["lane_b"]["worktrees_live"] is not None
    assert active["lane_b"]["branches_unlanded"] is not None
    assert "concurrency_stats" not in active
