"""Density harness meter — visible-context steer (todo:conductor-row-density-hop)."""

from __future__ import annotations

import json
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
    density_harness_record_patch,
    read_density_harness_meter,
    should_steer_density_hop,
    steer_threshold_tokens,
)
from services.git_integration_worker.cursor_sdk_steer_inject import SteerDepositResult
from services.git_integration_worker.cursor_sdk_stream_capture import observe_run_stream
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_GROK = "cursor/grok-4.7"
_THRESHOLD = 76_800  # 256_000 × 0.30


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GIW_DENSITY_HARNESS_VISIBLE_FRACTION", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "14116",
        "model": _GROK,
        "dispatch_id": "dens-conductor-1",
        "execution_id": "exec-dens-1",
        "message": "conductor",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admit_row(
    ledger: CursorDispatchLedger,
    req: CursorDispatchRequest,
    *,
    contract: str = "conductor",
) -> None:
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
        contract=contract,
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:density-steer-fixture",
        source_ref="todo:density-steer-fixture",
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch={"hop_entry_gate": "G3", "model": _GROK},
    )


def _record_json(ledger: CursorDispatchLedger, dispatch_id: str) -> dict:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return json.loads(row["record_json"])


def test_threshold_grok_default_fraction() -> None:
    assert steer_threshold_tokens(model=_GROK) == _THRESHOLD


def test_conductor_at_threshold_steers_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_row(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    deposits: list[SteerDepositResult] = []

    def _deposit(**kwargs: object) -> SteerDepositResult:
        result = SteerDepositResult(
            dispatch_id=str(kwargs["dispatch_id"]),
            entry_id="e1",
            authority_turn_id="9",
            spool_path="/tmp/spool",
        )
        deposits.append(result)
        return result

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        _deposit,
    )
    first = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    second = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert first["steered"] is True
    assert second["steered"] is False
    assert len(deposits) == 1


def test_conductor_at_threshold_steers_directive_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_row(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    captured: dict[str, str] = {}

    def _deposit(**kwargs: object) -> SteerDepositResult:
        captured["directive"] = str(kwargs["directive"])
        return SteerDepositResult(
            dispatch_id=req.dispatch_id,
            entry_id="e1",
            authority_turn_id="9",
            spool_path="/tmp/spool",
        )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        _deposit,
    )
    maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert "stop: ROW_HOP" in captured["directive"]
    assert "Next-pickup: G3" in captured["directive"]


def test_conductor_at_76799_no_steer(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_row(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD - 1],
        ledger=ledger,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        MagicMock(),
    )
    out = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert out["steered"] is False


def test_non_conductor_no_steer(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="dens-freeform-1", execution_id="exec-f1")
    _admit_row(ledger, req, contract="freeform")
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD + 1],
        ledger=ledger,
    )
    mock = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        mock,
    )
    out = maybe_tick_density_harness_steer(dispatch_id=req.dispatch_id)
    assert out["steered"] is False
    mock.assert_not_called()


def test_model_without_window_no_steer() -> None:
    reading = read_density_harness_meter(
        json.dumps(
            density_harness_record_patch(
                {"latest_visible_estimate": 999_999, "model_calls": 1}
            )
        ),
        model="not-a-valid-cursor-model",
    )
    assert should_steer_density_hop(reading, model="not-a-valid-cursor-model") is False


def test_empty_stream_no_steer_no_park(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyRun:
        def stream(self):
            return iter([])

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.deposit_steer_directive",
        MagicMock(),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.signal_park",
        MagicMock(),
    )
    capture = observe_run_stream(
        _EmptyRun(),
        dispatch_id="empty-1",
        thread_id="14116",
        resolved_model=_GROK,
    )
    assert capture.tool_call_count == 0
    reading = read_density_harness_meter("{}", model=_GROK)
    assert reading.latest_visible_estimate == 0
    assert reading.model_calls == 0


def test_delivered_steer_plus_eight_tools_parks_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_row(ledger, req)
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=[_THRESHOLD],
        ledger=ledger,
    )
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch(
            {
                "density_steer_deposited": True,
                "density_steer_delivered": True,
                "density_steer_delivered_at_tool_count": 10,
            }
        ),
    )
    parks: list[int] = []

    def _park(*_a: object, **_k: object) -> MagicMock:
        parks.append(1)
        return MagicMock(refusal=None)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.signal_park",
        _park,
    )
    out_before = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=17
    )
    out_at = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=18
    )
    out_again = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=25
    )
    assert out_before.get("parked") is not True
    assert out_at.get("parked") is True
    assert out_again.get("parked") is not True
    assert len(parks) == 1


def test_outstanding_cdp_generate_defers_ignored_steer_park(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """a:37450 — do not cancel a conductor that still owes a CDP harvest."""
    from datetime import UTC, datetime

    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="dens-cdp-open", execution_id="exec-cdp-open")
    _admit_row(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch(
            {
                "density_steer_deposited": True,
                "density_steer_delivered": True,
                "density_steer_delivered_at_tool_count": 10,
            }
        ),
    )
    spool = tmp_path / "steer-spool"
    spool.mkdir()
    fired = {
        "execution_id": "cdp-exec-1",
        "thread_id": "14724",
        "after_turn": 4,
        "from_agent": "web-anthropic",
        "model": "cdp/opus-5.5",
        "fired_at": datetime.now(UTC).isoformat(),
    }
    ledger_path = spool / f"{req.dispatch_id}.cdp-generates.jsonl"
    ledger_path.write_text(json.dumps(fired) + "\n", encoding="utf-8")
    mock = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.signal_park",
        mock,
    )
    deferred = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=30
    )
    assert deferred.get("parked") is not True
    mock.assert_not_called()
    with ledger_path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "kind": "received",
                    "execution_id": "cdp-exec-1",
                    "tool": "wait",
                    "received_at": datetime.now(UTC).isoformat(),
                }
            )
            + "\n"
        )
    parked = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=30
    )
    assert parked.get("parked") is True
    mock.assert_called_once()
    assert mock.call_args.kwargs["reason"] == "density-harness-ignored-steer"


def test_terminal_before_plus_eight_no_park(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req()
    _admit_row(ledger, req)
    ledger.merge_record_json(
        dispatch_id=req.dispatch_id,
        patch=density_harness_control_patch(
            {
                "density_steer_deposited": True,
                "density_steer_delivered": True,
                "density_steer_delivered_at_tool_count": 10,
            }
        ),
    )
    ledger.mark_terminal(
        dispatch_id=req.dispatch_id,
        terminal_status="completed",
    )
    mock = MagicMock()
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_row_density_harness_meter.signal_park",
        mock,
    )
    out = maybe_tick_density_harness_steer(
        dispatch_id=req.dispatch_id, tool_call_count=30
    )
    assert out.get("parked") is not True
    mock.assert_not_called()


def test_series_capped_at_200_after_250_calls() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="dens-series", execution_id="exec-series")
    _admit_row(ledger, req)
    estimates = list(range(1, 251))
    apply_synthetic_density_trajectory(
        dispatch_id=req.dispatch_id,
        call_estimates=estimates,
        ledger=ledger,
    )
    data = _record_json(ledger, req.dispatch_id)["density_harness"]
    assert data["model_calls"] == 250
    assert len(data["visible_series"]) == 200
    assert data["latest_visible_estimate"] == 250
    assert data["running_visible_sum"] == sum(estimates)


def test_stream_hook_persists_on_model_call_boundary() -> None:
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="dens-hook", execution_id="exec-hook")
    _admit_row(ledger, req)
    hook = DensityHarnessStreamHook(
        dispatch_id=req.dispatch_id,
        user_text="x" * 400,
        ledger=ledger,
    )
    hook.note_prose("a" * 400)
    hook.note_tool_call(arg_bytes=400, result_bytes=400, status="completed")
    hook.note_prose("b" * 400)
    hook.flush()
    data = _record_json(ledger, req.dispatch_id)["density_harness"]
    assert data["model_calls"] >= 1
    assert data["latest_visible_estimate"] > 0
    assert data["visible_series"]


def test_row_hop_planned_infer_stays_planned() -> None:
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
        _infer_hop_reason,
    )

    assert (
        _infer_hop_reason(
            closeout_tokens=frozenset({"ROW_HOP"}),
            terminal_status="completed",
        )
        == "planned"
    )
