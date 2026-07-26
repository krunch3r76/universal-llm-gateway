"""Click-time reconcile (G5.1 slice 2)."""

from __future__ import annotations

from scripts.model_manager.ui.dispatch_monitor.core import fingerprint as fingerprint_mod
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.tests.conftest import fixture_path
from scripts.model_manager.ui.dispatch_monitor.core.__main__ import main as core_main
from scripts.model_manager.ui.dispatch_monitor.ulg.controller import MonitorController
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_events import (
    events_from_ledger,
    source_failure_event,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_on_click import ReconcileOnClick
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_sources import SourceOutcome

_SAMPLE_BUS = {
    "id": "2678",
    "dispatch_links": [
        {"execution_id": "disp-inflight-1", "terminal_status": None, "pipeline_id": "p1"}
    ],
}
_SAMPLE_LEDGER = {
    "thread_id": "2678",
    "dispatch_id": "disp-inflight-1",
    "execution_id": "disp-inflight-1",
    "status": "running",
}
_SAMPLE_CORTEX = {
    "uri": "cortex://notes/system/threads/2678-charter-scoreboard.md",
    "content": "# scoreboard\n",
    "line_count": 2,
}


def _reconcile_with(
    *,
    bus=_SAMPLE_BUS,
    ledger=_SAMPLE_LEDGER,
    cortex=_SAMPLE_CORTEX,
    bus_ok: bool = True,
    ledger_ok: bool = True,
    cortex_ok: bool = True,
) -> ReconcileOnClick:
    def _bus(_thread: str) -> SourceOutcome:
        if not bus_ok:
            return SourceOutcome("bus", False, None, "timeout")
        return SourceOutcome("bus", True, bus)

    def _ledger(_thread: str) -> SourceOutcome:
        if not ledger_ok:
            return SourceOutcome("ledger", False, None, "timeout")
        return SourceOutcome("ledger", True, ledger)

    def _cortex(_thread: str, _bus_data) -> SourceOutcome:  # noqa: ANN001
        if not cortex_ok:
            return SourceOutcome("cortex", False, {"uri": "cortex://x"}, "missing")
        return SourceOutcome("cortex", True, cortex)

    return ReconcileOnClick(bus_fetch=_bus, ledger_fetch=_ledger, cortex_fetch=_cortex)


def test_clock_ticks_never_invoke_reconcile(monkeypatch) -> None:
    calls = {"n": 0}
    reconcile = _reconcile_with()

    def _subject(subject: str):
        calls["n"] += 1
        return reconcile.reconcile_subject(subject)

    monkeypatch.setattr(reconcile, "reconcile_subject", _subject)
    controller = MonitorController(reconcile=reconcile, tick_s=0.01)
    for _ in range(5):
        controller.tick()
    assert calls["n"] == 0


def test_reconcile_applies_events_with_provenance_via_controller() -> None:
    controller = MonitorController(reconcile=_reconcile_with())
    result = controller.trigger_reconcile("2678")
    assert result["applied"] >= 1
    frame = controller.model.derive(1_756_000_010_000)
    row = next(r for r in frame.sdk if r.dispatch_id == "disp-inflight-1")
    assert row.provenance == "reconciled"
    assert row.state == "running"


def test_live_signal_not_clobbered_by_reconcile() -> None:
    controller = MonitorController(reconcile=_reconcile_with())
    controller.model.apply(
        Event(
            signals.SDK_WORKER_PROGRESS,
            1_756_000_001_000,
            {"execution_id": "disp-inflight-1", "stall_stage": "thinking"},
        )
    )
    controller.trigger_reconcile("2678")
    row = next(
        r
        for r in controller.model.derive(1_756_000_002_000).sdk
        if r.dispatch_id == "disp-inflight-1"
    )
    assert row.provenance == "signal"


def test_subject_scoped_no_full_world_refresh() -> None:
    seen: list[str] = []

    def _ledger(thread_id: str) -> SourceOutcome:
        seen.append(thread_id)
        return SourceOutcome("ledger", True, _SAMPLE_LEDGER)

    reconcile = ReconcileOnClick(
        bus_fetch=lambda tid: SourceOutcome("bus", True, _SAMPLE_BUS),
        ledger_fetch=_ledger,
        cortex_fetch=lambda tid, _: SourceOutcome("cortex", True, _SAMPLE_CORTEX),
    )
    reconcile.reconcile_subject("disp-inflight-1")
    assert seen == ["disp-inflight-1"]


def test_one_source_failure_does_not_abort_others() -> None:
    reconcile = _reconcile_with(bus_ok=False, ledger_ok=True, cortex_ok=True)
    events, outcomes = reconcile.reconcile_subject("2678")
    assert len(outcomes) == 3
    assert outcomes[0].ok is False
    assert outcomes[1].ok is True
    assert outcomes[2].ok is True
    assert any(e.signal == signals.MONITOR_RECONCILE_SOURCE_FAILED for e in events)
    assert any(e.signal == signals.MONITOR_META_SDK_STARTED for e in events)


def test_reconcile_failure_surfaces_as_attention_item() -> None:
    model = Model()
    model.apply(
        source_failure_event(
            subject="2678",
            source="cortex",
            error="missing_content",
            ts_unix_ms=1_000,
        )
    )
    frame = model.derive(2_000)
    match = [
        item
        for item in frame.attention
        if item.kind == "monitor.reconcile.source_failed"
    ]
    assert len(match) == 1
    assert "cortex" in match[0].detail


def test_reconcile_replay_twice_identical_fingerprint() -> None:
    reconcile = _reconcile_with()

    def _run() -> str:
        model = Model()
        for event in reconcile.reconcile_subject("2678")[0]:
            model.apply(event)
        return model.derive(1_756_000_010_000).fingerprint

    assert _run() == _run()
    first = Model()
    second = Model()
    events = reconcile.reconcile_subject("2678")[0]
    first.apply_all(events)
    second.apply_all(events)
    now = 1_756_000_010_000
    assert first.derive(now).fingerprint == second.derive(now).fingerprint
    assert fingerprint_mod.fingerprint_payload(first.derive(now)) == fingerprint_mod.fingerprint_payload(
        second.derive(now)
    )


def test_fixture_watch_runs_without_reconcile_port(capsys) -> None:
    exit_code = core_main(
        [
            "--watch",
            fixture_path("charter-admit-run-terminal.jsonl"),
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    assert capsys.readouterr().out.strip()


def test_unwired_reconcile_port_marks_attention_not_silent() -> None:
    controller = MonitorController(reconcile=None)
    result = controller.trigger_reconcile("2678")
    assert result.get("error") == "reconcile_unwired"
    frame = controller.model.derive(1_000)
    assert any(
        item.kind == "monitor.reconcile.source_failed" for item in frame.attention
    )


def test_ledger_no_row_emits_failure_not_empty_success() -> None:
    events = events_from_ledger(
        {"thread_id": "9999", "status": None},
        subject="9999",
        ts_unix_ms=1_000,
    )
    assert len(events) == 1
    assert events[0].signal == signals.MONITOR_RECONCILE_SOURCE_FAILED


def test_meta_sdk_started_signal_name() -> None:
    assert signals.MONITOR_META_SDK_STARTED == "monitor.meta.sdk_started"
    assert "frontier.sdk.worker.started" not in signals.ALL_HANDLED
