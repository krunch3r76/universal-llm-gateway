"""Regression tests for agent-bus:14122 review WITHHOLD findings (435e543a)."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from services.git_integration_worker.conductor_hop_watchdog import (
    maybe_tick_density_harness_steer,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_row_density_harness_meter import (
    DensityHarnessStreamHook,
    apply_synthetic_density_trajectory,
    density_harness_control_patch,
    maybe_deposit_density_steer,
    read_density_harness_meter,
)
from services.git_integration_worker.cursor_sdk_steer_inject import SteerDepositResult
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_GROK = "cursor/grok-4.7"
_THRESHOLD = 76_800


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "14122",
        "model": _GROK,
        "dispatch_id": "withhold-fixture",
        "execution_id": "exec-withhold",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit(ledger: CursorDispatchLedger, req: CursorDispatchRequest) -> None:
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="grok-4.7",
        ),
        contract="conductor",
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:withhold",
        source_ref="todo:withhold",
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"hop_entry_gate": "G3", "model": _GROK},
    )


def test_finding1_trajectory_writes_do_not_erase_steer_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1: interleave stream deltas + ticks; latch and entry id survive."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch(
            {
                "density_steer_deposited": True,
                "density_steer_entry_id": "entry-pinned",
                "density_steer_delivered": True,
                "density_steer_delivered_at_tool_count": 3,
            }
        ),
    )
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    deposits: list[str] = []

    def _deposit(**kwargs: object) -> SteerDepositResult:
        deposits.append(str(kwargs["dispatch_id"]))
        return SteerDepositResult(
            dispatch_id=req.dispatch_id,
            entry_id="new",
            authority_turn_id="1",
            spool_path="/tmp/x",
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        _deposit,
    )
    hook = DensityHarnessStreamHook(
        dispatch_id=req.dispatch_id,
        user_text="prompt",
        ledger=ledger,
    )
    for _ in range(5):
        hook.note_prose("delta " * 50)
        maybe_tick_density_harness_steer(
            dispatch_id=req.dispatch_id, tool_call_count=hook._stream_tool_call_count
        )
    hook.note_tool_call(arg_bytes=100, result_bytes=100, status="completed")
    maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=hook._stream_tool_call_count
    )
    hook.flush()
    import json

    with ledger._connect() as conn:
        rec = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (req.dispatch_id,),
        ).fetchone()
    data = json.loads(rec["record_json"])
    ctrl = data["density_harness_control"]
    assert ctrl["density_steer_deposited"] is True
    assert ctrl["density_steer_entry_id"] == "entry-pinned"
    assert deposits == []
    reading = read_density_harness_meter(rec["record_json"], model=_GROK)
    assert reading.latest_visible_estimate >= _THRESHOLD


def test_finding2_concurrent_deposit_claim_allows_one_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 2: live tick + sweep race → one ``deposit_steer_directive``."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    barrier = threading.Barrier(2)
    calls: list[str] = []
    lock = threading.Lock()

    def _deposit(**kwargs: object) -> SteerDepositResult:
        with lock:
            calls.append(str(kwargs.get("entry_id", "")))
        return SteerDepositResult(
            dispatch_id=req.dispatch_id,
            entry_id="e-race",
            authority_turn_id="9",
            spool_path="/tmp/x",
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        _deposit,
    )

    def _worker(submitted_id: str) -> None:
        barrier.wait()
        with ledger._connect() as conn:
            raw = conn.execute(
                "SELECT * FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (req.dispatch_id,),
            ).fetchone()
        mapped = {k: raw[k] for k in raw.keys()}
        maybe_deposit_density_steer(mapped, submitted_id=submitted_id, ledger=ledger)

    t1 = threading.Thread(target=_worker, args=("claim-a",))
    t2 = threading.Thread(target=_worker, args=("claim-b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(calls) == 1


def test_finding3_fresh_hook_loads_existing_control_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 3: new hook after deposit must not reset steer latch."""
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch({"density_steer_deposited": True}),
    )
    mock = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        mock,
    )
    DensityHarnessStreamHook(dispatch_id=req.dispatch_id, user_text="fresh", ledger=ledger)
    out = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert out["steered"] is False
    mock.assert_not_called()


def test_finding6_trajectory_persist_only_at_model_call_boundaries() -> None:
    """Finding 6: five prose deltas + one tool + flush → one trajectory merge."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="persist-count", execution_id="exec-pc")
    _admit(ledger, req)
    traj_writes: list[dict] = []
    real = ledger.merge_record_json_subobject

    def _count_subobject(*, dispatch_id: str, subkey: str, patch: dict) -> None:
        if subkey == "density_harness":
            traj_writes.append(dict(patch))
        return real(dispatch_id=dispatch_id, subkey=subkey, patch=patch)

    ledger.merge_record_json_subobject = _count_subobject  # type: ignore[method-assign]
    hook = DensityHarnessStreamHook(
        dispatch_id=req.dispatch_id,
        user_text="u" * 16,
        ledger=ledger,
    )
    for _ in range(5):
        hook.note_prose("p" * 40)
    hook.note_tool_call(arg_bytes=80, result_bytes=80, status="completed")
    hook.flush()
    assert len(traj_writes) == 1


def test_park_uses_persisted_stream_tool_call_count_without_live_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """+8 enforcement reads ``density_harness_control.stream_tool_call_count``."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="park-persist", execution_id="exec-pp")
    _admit(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch(
            {
                "density_steer_deposited": True,
                "density_steer_delivered": True,
                "density_steer_delivered_at_tool_count": 10,
                "stream_tool_call_count": 18,
            }
        ),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.signal_park",
        lambda *_a, **_k: MagicMock(refusal=None),
    )
    out = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert out.get("parked") is True
