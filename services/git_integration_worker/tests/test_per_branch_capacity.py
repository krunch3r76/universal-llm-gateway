"""Mode B derived standard capacity — per-branch parallelism (AC1–AC8)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot,
    reset_capacity_derivation_state,
    sdk_dispatch_gate_stats,
)
from services.git_integration_worker.cursor_sdk_lane_regime import set_lane_b_regime
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    register_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_workspace import (
    isolated_write_headroom,
    write_lease_slots,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_capacity_derivation_state()
    set_lane_b_regime(active=True)
    monkeypatch.setenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", "4")
    yield
    CursorDispatchLedger._instance = None
    reset_capacity_derivation_state()
    set_lane_b_regime(active=False)


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t-pbc",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-pbc",
        "execution_id": "exec-disp-pbc",
        "message": "hello",
        "lane": "B",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    source_repo: str,
    lease_key: str,
) -> CursorDispatchResponse | None:
    return ledger.admit(
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


def _seed_live_worktree(
    tmp_path: Path,
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    idx: int,
) -> Path:
    wt = tmp_path / "worktrees" / f"wt-{idx}"
    wt.mkdir(parents=True, exist_ok=True)
    register_dispatch_worktree(
        dispatch_id=dispatch_id,
        worktree_path=wt,
        branch_name=f"branch-{idx}",
        branch_point="abc123",
    )
    req = _req(
        dispatch_id=dispatch_id,
        thread_id=f"t-live-{idx}",
        message=f"work-{idx}",
    )
    _admit(
        ledger,
        req,
        source_repo=str(tmp_path / "source"),
        lease_key=str(wt),
    )
    return wt


@pytest.mark.asyncio
async def test_ac1_distinct_keys_concurrent() -> None:
    """AC1: distinct lease keys acquire standard capacity concurrently under regime-ON."""
    await acquire_sdk_dispatch_slot(dispatch_id="lane-b-a")
    await acquire_sdk_dispatch_slot(dispatch_id="lane-b-b")
    stats = sdk_dispatch_gate_stats()
    assert int(stats["standard"]["active"]) == 2
    assert int(stats["standard"]["limit"]) >= 2
    await release_sdk_dispatch_slot(dispatch_id="lane-b-a")
    await release_sdk_dispatch_slot(dispatch_id="lane-b-b")


def test_ac2_same_key_write_lease_queue(tmp_path: Path) -> None:
    """AC2: same worktree path queues on write_lease, not capacity:standard."""
    repo = str(tmp_path / "source")
    key = str(tmp_path / "worktrees" / "shared")
    Path(key).mkdir(parents=True)
    ledger = CursorDispatchLedger.instance()

    _admit(ledger, _req(dispatch_id="holder", thread_id="t-holder"), source_repo=repo, lease_key=key)
    queued = _admit(
        ledger,
        _req(dispatch_id="waiter", thread_id="t-waiter"),
        source_repo=repo,
        lease_key=key,
    )
    assert queued is not None
    assert queued.status == "queued"

    snap = ledger.lease_snapshot(lease_key=key)
    assert snap["queued"][0]["queued_on"] == f"write_lease:{key}"
    assert not str(snap["queued"][0]["queued_on"]).startswith("capacity:")


def test_ac3_regime_off_limit_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: regime OFF keeps standard limit 1."""
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    set_lane_b_regime(active=False)
    reset_capacity_derivation_state()
    stats = sdk_dispatch_gate_stats()
    assert int(stats["standard"]["limit"]) == 1


def test_ac4_busy_status_honesty() -> None:
    """AC4: advertised standard.limit equals derived headroom under regime-ON."""
    CursorDispatchLedger.instance()
    derived = isolated_write_headroom()
    stats = sdk_dispatch_gate_stats()
    assert int(stats["standard"]["limit"]) == derived
    assert int(stats["write_capacity"]) == derived
    assert int(stats["configured_headroom"]) == 4
    detail = stats["write_capacity_detail"]
    assert isinstance(detail, dict)
    assert int(detail["lane_b"]["slots"]) == derived


def test_ac5_i1_clamp_edge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: headroom below ceiling clamps limit and emits I1 transition event."""
    monkeypatch.setenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", "4")
    reset_capacity_derivation_state()
    emitted: list[object] = []

    def _capture(event: object) -> None:
        emitted.append(event)

    with patch(
        "services.git_integration_worker.cursor_sdk_events._emit",
        side_effect=_capture,
    ):
        CursorDispatchLedger.instance()
        ok_stats = sdk_dispatch_gate_stats()
        assert ok_stats["capacity_disposition"] == "ok"

        ledger = CursorDispatchLedger.instance()
        for idx in range(3):
            _seed_live_worktree(tmp_path, ledger, dispatch_id=f"live-{idx}", idx=idx)

        clamp_stats = sdk_dispatch_gate_stats()

    assert clamp_stats["capacity_disposition"] == "clamp"
    assert int(clamp_stats["standard"]["limit"]) == 1
    signals = [getattr(e, "signal", None) for e in emitted]
    assert "frontier.sdk.gate.i1_clamp_transition" in signals
    assert "frontier.sdk.gate.limit_derived" in signals
    transition = next(
        e for e in emitted if getattr(e, "signal", None) == "frontier.sdk.gate.i1_clamp_transition"
    )
    assert transition.payload["from_disposition"] == "ok"
    assert transition.payload["to_disposition"] == "clamp"


def test_ac8_falsifier_unconstructible() -> None:
    """AC8: active=1, limit=1, lane_b.slots>=2 with distinct keys is unconstructible."""
    CursorDispatchLedger.instance()
    stats = sdk_dispatch_gate_stats()
    standard = stats["standard"]
    detail = stats["write_capacity_detail"]
    assert isinstance(detail, dict)
    lane_b_slots = int(detail["lane_b"]["slots"])
    std_limit = int(standard["limit"])
    std_active = int(standard["active"])

    falsifier_triple = std_active == 1 and std_limit == 1 and lane_b_slots >= 2
    if falsifier_triple:
        pytest.fail("Mode B §6 falsifier state must be unconstructible under regime-ON")

    assert std_limit >= 1
    if std_limit >= 2:
        async def _hold_two() -> None:
            await acquire_sdk_dispatch_slot(dispatch_id="fals-a")
            await acquire_sdk_dispatch_slot(dispatch_id="fals-b")
            s = sdk_dispatch_gate_stats()
            assert int(s["standard"]["active"]) == 2
            await release_sdk_dispatch_slot(dispatch_id="fals-a")
            await release_sdk_dispatch_slot(dispatch_id="fals-b")

        asyncio.run(_hold_two())


def test_regime_on_write_lease_slots_ignores_gate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lane-B under regime-ON derives from headroom, not gate_limit param."""
    monkeypatch.setenv("CURSOR_SDK_ISOLATED_WRITE_CEILING", "4")
    assert write_lease_slots("B", gate_limit=99) == 4
