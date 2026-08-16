"""Tests for concurrency-policy-honesty Rank-1 land (default-inert switch OFF)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    resolve_admit_lane,
)
from services.git_integration_worker.cursor_sdk_concurrency_meter import (
    DispatchInterval,
    count_overlap_pairs,
    is_contract_unknown,
    is_declared_write_implement,
    is_reaper_inflated_terminal,
    peak_concurrent_for_lane,
)
from services.git_integration_worker.cursor_sdk_concurrency_posture import (
    derive_concurrency_posture,
    lease_is_isolated_worktree,
    operator_multi_a_enabled,
    write_lease_slot_limit,
)
from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_gate_stats
from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", raising=False)
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    yield tmp_path
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)


def test_operator_multi_a_disabled_by_default() -> None:
    assert operator_multi_a_enabled() is False


def test_lane_b_without_isolated_worktree_is_not_multi_b(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert (
        derive_concurrency_posture(
            admit_lane="B",
            gate_lane="standard",
            read_only=False,
            nest_under=None,
            worktree_path=repo,
            source_repo=str(repo),
        )
        is None
    )
    assert (
        lease_is_isolated_worktree(lease_key=str(repo), source_repo=str(repo)) is False
    )


def test_derive_posture_multi_b_when_worktree_isolated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    wt = tmp_path / "worktrees" / "d1"
    repo.mkdir()
    wt.mkdir(parents=True)
    assert (
        derive_concurrency_posture(
            admit_lane="B",
            gate_lane="standard",
            read_only=False,
            nest_under=None,
            worktree_path=wt,
            source_repo=str(repo),
        )
        == "multi_b"
    )


def test_lane_a_slot_limit_inert_without_switch() -> None:
    assert write_lease_slot_limit(admit_lane="A", posture="multi_a_operator") == 1
    assert write_lease_slot_limit(admit_lane="A", posture="sole_a") == 1


def test_lane_b_slot_limit_is_one() -> None:
    assert write_lease_slot_limit(admit_lane="B", posture="multi_b") == 1
    assert write_lease_slot_limit(admit_lane="B", posture="nest_child") == 1


def test_lane_a_slot_limit_when_switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    assert write_lease_slot_limit(admit_lane="A", posture="multi_a_operator") == 3
    assert write_lease_slot_limit(admit_lane="A", posture="sole_a") == 1


def test_derive_posture_sole_a_when_switch_off() -> None:
    assert (
        derive_concurrency_posture(
            admit_lane="A",
            gate_lane="operator",
            read_only=False,
            nest_under=None,
            worktree_path=None,
        )
        == "sole_a"
    )


def test_derive_posture_nest_child() -> None:
    assert (
        derive_concurrency_posture(
            admit_lane="A",
            gate_lane="operator",
            read_only=False,
            nest_under="parent-1",
            worktree_path=None,
        )
        == "nest_child"
    )


def test_derive_posture_read_only_exempt() -> None:
    assert (
        derive_concurrency_posture(
            admit_lane="A",
            gate_lane="operator",
            read_only=True,
            nest_under=None,
            worktree_path=None,
        )
        is None
    )


def test_derive_posture_multi_a_when_switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", "1")
    assert (
        derive_concurrency_posture(
            admit_lane="A",
            gate_lane="operator",
            read_only=False,
            nest_under=None,
            worktree_path=None,
        )
        == "multi_a_operator"
    )


def test_resolve_admit_lane_unknown_when_both_null() -> None:
    assert (
        resolve_admit_lane(record_json="{}", lease_key=None, source_repo=None)
        == "unknown"
    )


def test_d1_null_contract_not_declared_implement() -> None:
    assert is_contract_unknown(contract=None, read_only=False) is True
    assert is_declared_write_implement(contract=None, read_only=False) is False


def test_d3_reaper_inflated_terminal() -> None:
    assert is_reaper_inflated_terminal(
        terminal_at="2026-06-17T07:16:59+00:00",
        last_heartbeat_at="2026-06-15T23:28:49+00:00",
    )


def test_f_historical_corrected_peak_excludes_null_contract() -> None:
    intervals = [
        DispatchInterval(
            dispatch_id="null-contract",
            contract=None,
            read_only=False,
            started_at="2026-06-16T00:10:00+00:00",
            terminal_at="2026-06-16T00:20:00+00:00",
            lane="A",
        ),
        DispatchInterval(
            dispatch_id="impl-a",
            contract="implement",
            read_only=False,
            started_at="2026-06-16T00:10:00+00:00",
            terminal_at="2026-06-16T00:20:00+00:00",
            lane="A",
        ),
        DispatchInterval(
            dispatch_id="impl-b",
            contract="implement",
            read_only=False,
            started_at="2026-06-16T00:12:00+00:00",
            terminal_at="2026-06-16T00:18:00+00:00",
            lane="A",
        ),
    ]
    assert (
        peak_concurrent_for_lane(intervals, write_only=True, lane="A", corrected=True)
        == 2
    )
    assert (
        peak_concurrent_for_lane(intervals, write_only=True, lane="A", corrected=False)
        == 3
    )


def test_f_overlap_lane_scoped() -> None:
    intervals = [
        DispatchInterval(
            dispatch_id="a1",
            contract="implement",
            read_only=False,
            started_at="2026-06-16T00:10:00+00:00",
            terminal_at="2026-06-16T00:20:00+00:00",
            lane="A",
        ),
        DispatchInterval(
            dispatch_id="b1",
            contract="implement",
            read_only=False,
            started_at="2026-06-16T00:11:00+00:00",
            terminal_at="2026-06-16T00:19:00+00:00",
            lane="B",
        ),
    ]
    assert (
        count_overlap_pairs(intervals, write_only=True, lane="A", corrected=True) == 0
    )
    assert (
        count_overlap_pairs(intervals, write_only=True, lane=None, corrected=True) == 1
    )


def test_frontier_write_lease_acquired_emitted_on_admit() -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        emit_write_lease_acquired,
    )

    emitted: list[dict] = []

    def _capture(event: object) -> None:
        emitted.append(getattr(event, "payload", {}))

    with patch(
        "services.git_integration_worker.cursor_sdk_events._emit",
        side_effect=_capture,
    ):
        emit_write_lease_acquired(dispatch_id="d1", source_repo="/repo")

    assert emitted == [{"dispatch_id": "d1", "source_repo": "/repo"}]


def test_posture_stamped_on_admit_record_json() -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id="posture-d1",
        execution_id="exec-posture-d1",
        message="hi",
        lane="A",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="posture-d1",
            thread_id="t1",
            model_id="composer-2.5",
            status="admitted",
        ),
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        concurrency_posture="sole_a",
        write_lease_slot_limit=1,
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("posture-d1",),
        ).fetchone()
    assert row is not None
    data = json.loads(row["record_json"])
    assert data.get("concurrency_posture") == "sole_a"


def test_f_standard_serial_second_operator_writer_queues() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"

    def _admit(dispatch_id: str) -> CursorDispatchResponse | None:
        req = CursorDispatchRequest(
            thread_id=dispatch_id,
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="hi",
            lane="A",
        )
        return ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent="cursor",
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=dispatch_id,
                thread_id=dispatch_id,
                model_id="composer-2.5",
            ),
            contract="implement",
            source_repo=repo,
            lease_key=repo,
            concurrency_posture="sole_a",
            write_lease_slot_limit=1,
        )

    _admit("op-holder")
    queued = _admit("op-waiter")
    assert queued is not None
    assert queued.status == "queued"


def test_f_capacity_split_live_writers_separate_from_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    gate = sdk_dispatch_gate_stats()
    assert gate["configured_headroom"] == 4
    assert gate["write_capacity"] == 1
    assert gate["live_writers"] == 0


def test_plan_nested_dispatch_uses_ledger_aligned_occupancy() -> None:
    """F-gate-admit / I-gate-ledger: nest plan honors ledger live_writers over gate.active."""
    from services.git_integration_worker.cursor_auto.gate_serialize import (
        plan_nested_dispatch,
    )

    call_idx = {"n": 0}

    def _stats(*, lane: str | None = None):
        call_idx["n"] += 1
        if lane == "operator":
            return {"active": 0, "queued": 0, "limit": 1}
        return {"active": 0, "queued": 0, "limit": 1, "live_writers": 1}

    with patch(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        side_effect=_stats,
    ):
        plan = plan_nested_dispatch(work_bounded=False)

    assert plan["gate"]["active"] == 1
    assert plan["gate"]["occupancy_source"] == "ledger_aligned"
    assert plan["action"] == "nest_park"
    assert call_idx["n"] >= 2


def test_multi_holder_snapshot_when_switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    ledger = CursorDispatchLedger.instance()
    repo = "/repo"

    for dispatch_id in ("op-a", "op-b"):
        req = CursorDispatchRequest(
            thread_id=dispatch_id,
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="hi",
            lane="A",
        )
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent="cursor",
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=dispatch_id,
                thread_id=dispatch_id,
                model_id="composer-2.5",
            ),
            contract="implement",
            source_repo=repo,
            lease_key=repo,
            concurrency_posture="multi_a_operator",
            write_lease_slot_limit=3,
        )

    snap = ledger.lease_snapshot(source_repo=repo)
    assert len(snap["active_holders"]) == 2
