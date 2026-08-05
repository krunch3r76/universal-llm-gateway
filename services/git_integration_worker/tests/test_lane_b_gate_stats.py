"""Lane-B S4 — per-lane write capacity reporting (AC-S4.*)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    active_by_lane_counts,
    evaluate_i1,
    resolve_admit_lane,
)
from services.git_integration_worker.cursor_sdk_gate import sdk_dispatch_gate_stats
from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
from services.git_integration_worker.cursor_sdk_workspace import write_lease_slots
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    yield
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-s4",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-s4",
        "execution_id": "exec-disp-s4",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_active(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str,
    lease_key: str,
    lane: str | None = None,
) -> None:
    if lane is not None:
        req = req.model_copy(update={"lane": lane})
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        lease_key=lease_key,
        contract="implement",
        read_only=False,
    )


def test_ac_s4_1_regime_off_write_capacity_and_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S4.1: regime off ⇒ write_capacity=1, capacity_disposition=clamp."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    set_lane_b_regime(active=False)

    gate = sdk_dispatch_gate_stats()
    assert gate["write_capacity"] == 1
    assert gate["capacity_disposition"] == "clamp"
    detail = gate["write_capacity_detail"]
    assert isinstance(detail, dict)
    assert detail["lane_a"]["slots"] == 1


def test_ac_s4_2_regime_on_write_capacity_and_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-S4.2: regime on ⇒ write_capacity=std+op; lane_a.slots always 1."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    set_lane_b_regime(active=True)

    gate = sdk_dispatch_gate_stats()
    assert gate["write_capacity"] == 4
    assert gate["capacity_disposition"] == "ok"
    detail = gate["write_capacity_detail"]
    assert isinstance(detail, dict)
    assert detail["lane_a"]["slots"] == 1
    assert detail["lane_b"]["slots"] == 4


def test_ac_s4_3_write_lease_slots_lane_b_gate_limits() -> None:
    """AC-S4.3: Lane-B equals gate_limit; no NotImplementedError."""
    for n in (1, 2, 4, 7):
        assert write_lease_slots("B", gate_limit=n) == n


def test_write_lease_slots_lane_a_inert_when_multi_a_switch_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-7 / AC1: switch OFF ⇒ Lane-A slots stay 1 regardless of gate_limit."""
    monkeypatch.delenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", raising=False)
    for n in (1, 2, 4, 7):
        assert write_lease_slots("A", gate_limit=n) == 1


def test_write_lease_slots_lane_a_honors_multi_a_when_switch_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-7 / AC1: switch ON ⇒ Lane-A slots honor operator dispatch concurrency."""
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", "1")
    monkeypatch.setenv("CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY", "3")
    assert write_lease_slots("A", gate_limit=7) == 3


def test_ac_s4_4_active_by_lane_matches_ledger(tmp_path: Path) -> None:
    """AC-S4.4: active_by_lane reflects seeded Lane-A and Lane-B writers."""
    repo = str(tmp_path / "source")
    lane_b_key = str(tmp_path / "worktrees" / "cursor-sdk-b1")
    ledger = CursorDispatchLedger.instance()

    _admit_active(
        ledger,
        _req(dispatch_id="lane-a-1"),
        source_repo=repo,
        lease_key=repo,
        lane="A",
    )
    _admit_active(
        ledger,
        _req(dispatch_id="lane-b-1"),
        source_repo=repo,
        lease_key=lane_b_key,
        lane="B",
    )
    _admit_active(
        ledger,
        _req(dispatch_id="lane-b-2"),
        source_repo=repo,
        lease_key=str(tmp_path / "worktrees" / "cursor-sdk-b2"),
        lane="B",
    )

    gate = sdk_dispatch_gate_stats()
    assert gate["active_by_lane"] == {"A": 1, "B": 2, "unknown": 0}


def test_resolve_admit_lane_from_record_json() -> None:
    assert (
        resolve_admit_lane(
            record_json='{"lane":"B"}',
            lease_key="/repo",
            source_repo="/repo",
        )
        == "B"
    )
    assert (
        resolve_admit_lane(
            record_json="{}",
            lease_key="/repo/worktree",
            source_repo="/repo",
        )
        == "B"
    )
    assert (
        resolve_admit_lane(
            record_json="{}",
            lease_key="/repo",
            source_repo="/repo",
        )
        == "A"
    )


def test_active_by_lane_counts_helper() -> None:
    rows = [
        {
            "record_json": '{"lane":"A"}',
            "lease_key": "/repo",
            "source_repo": "/repo",
        },
        {
            "record_json": '{"lane":"B"}',
            "lease_key": "/wt",
            "source_repo": "/repo",
        },
    ]
    assert active_by_lane_counts(rows) == {"A": 1, "B": 1, "unknown": 0}


def test_evaluate_i1_ok_and_clamp() -> None:
    assert evaluate_i1(1, 3, headroom=4) == "ok"
    assert evaluate_i1(1, 3, headroom=3) == "clamp"
