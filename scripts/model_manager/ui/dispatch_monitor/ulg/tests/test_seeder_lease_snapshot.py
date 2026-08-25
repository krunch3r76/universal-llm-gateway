"""Cold-start lease-snapshot reconcile (G5.1 slice 1)."""

from __future__ import annotations

from pathlib import Path

from scripts.model_manager.ui.dispatch_monitor.core import (
    fingerprint as fingerprint_mod,
)
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.board_lines import lease_body_lines
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.replay import JsonlEventSource
from scripts.model_manager.ui.dispatch_monitor.core.tests.conftest import fixture_path
from scripts.model_manager.ui.dispatch_monitor.ulg.seeder import seed_model
from scripts.model_manager.ui.dispatch_monitor.ulg.snapshot_events import (
    events_from_lease_snapshot,
    fold_status_failure_event,
)

_SAMPLE_SNAPSHOT = {
    "holder_dispatch_id": "disp-inflight-1",
    "holder_thread_id": "2678",
    "holder_resolved_model": "composer-2.5",
    "holder_status": "running",
    "holder_started_at": "2026-07-26T10:00:00+00:00",
    "holder_source_repo": "universal-llm-gateway",
    "queue_depth": 2,
}


def test_seed_calls_lease_snapshot_exactly_once(monkeypatch) -> None:
    calls = {"n": 0}

    def _audit(**_kwargs):
        return {}

    def _signals(*_args, **_kwargs):
        return []

    def _fetch(**_kwargs):
        calls["n"] += 1
        return _SAMPLE_SNAPSHOT

    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.charter_tick_audit",
        _audit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.signal_events",
        _signals,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.dispatch_monitor.ulg.seeder.fetch_lease_snapshot",
        _fetch,
    )

    seed_model(lambda _event: None)
    assert calls["n"] == 1


def test_snapshot_events_enter_model_via_apply_only() -> None:
    model = Model()
    for event in events_from_lease_snapshot(_SAMPLE_SNAPSHOT):
        model.apply(event)
    frame = model.derive(1_756_000_000_000)
    dispatch = next(r for r in frame.sdk if r.dispatch_id == "disp-inflight-1")
    assert dispatch.provenance == "reconciled"
    assert dispatch.state == "running"
    assert frame.health.queue_depth == 2
    assert frame.health.fold_status == "seeded"
    assert frame.health.lease_holder == "disp-inflight-1"
    assert frame.health.lease_thread_id == "2678"
    assert frame.health.lease_model == "composer-2.5"
    assert frame.health.lease_heartbeat_age_ms is not None
    body = lease_body_lines(frame.health)[0][0]
    assert "holder=" in body
    assert "th=" in body
    assert "model=" in body
    assert "hb=" in body


def test_empty_snapshot_yields_holder_dash() -> None:
    model = Model()
    for event in events_from_lease_snapshot({"queue_depth": 0}):
        model.apply(event)
    frame = model.derive(1_000)
    assert frame.health.lease_holder is None
    body = lease_body_lines(frame.health)[0][0]
    assert "holder=-" in body


def test_real_signal_upgrades_provenance_and_snapshot_does_not_clobber() -> None:
    model = Model()
    for event in events_from_lease_snapshot(_SAMPLE_SNAPSHOT):
        model.apply(event)
    model.apply(
        Event(
            signals.SDK_WORKER_PROGRESS,
            1_756_000_001_000,
            {"execution_id": "disp-inflight-1", "stall_stage": "thinking"},
        )
    )
    upgraded = model.derive(1_756_000_002_000)
    row = next(r for r in upgraded.sdk if r.dispatch_id == "disp-inflight-1")
    assert row.provenance == "signal"

    for event in events_from_lease_snapshot(_SAMPLE_SNAPSHOT):
        model.apply(event)
    after_dup = model.derive(1_756_000_003_000)
    row = next(r for r in after_dup.sdk if r.dispatch_id == "disp-inflight-1")
    assert row.provenance == "signal"


def test_snapshot_plus_replay_burst_yields_one_sdk_row() -> None:
    model = Model()
    for event in events_from_lease_snapshot(_SAMPLE_SNAPSHOT):
        model.apply(event)
    model.apply(
        Event(
            signals.MONITOR_META_SDK_STARTED,
            1_756_000_000_500,
            {
                "execution_id": "disp-inflight-1",
                "thread_id": "2678",
                "model": "composer-2.5",
            },
            seq=9001,
        )
    )
    frame = model.derive(1_756_000_001_000)
    matches = [r for r in frame.sdk if r.dispatch_id == "disp-inflight-1"]
    assert len(matches) == 1


def test_snapshot_fetch_failure_is_non_fatal_and_marks_suspect() -> None:
    model = Model()
    model.apply(fold_status_failure_event(ts_unix_ms=1_000))
    frame = model.derive(2_000)
    assert frame.health.fold_status == "suspect"
    assert frame.sdk == ()


def test_snapshot_injected_replay_twice_identical_fingerprint() -> None:
    events = events_from_lease_snapshot(_SAMPLE_SNAPSHOT)

    def _run() -> str:
        model = Model()
        for event in events:
            model.apply(event)
        return model.derive(1_756_000_010_000).fingerprint

    assert _run() == _run()


def test_fixture_replay_still_deterministic_with_fold_status_default() -> None:
    first = Model()
    source = JsonlEventSource.from_path(fixture_path("gs2-dual-emitter.jsonl"))
    first.apply_all(source.records)
    now = source.max_ts()
    second = Model()
    second.apply_all(source.records)
    assert first.derive(now).fingerprint == second.derive(now).fingerprint
    assert (
        fingerprint_mod.fingerprint_payload(first.derive(now))
        == fingerprint_mod.fingerprint_payload(second.derive(now))
    )


def test_deletion_condition_findable_in_seeder() -> None:
    text = Path(__file__).resolve().parents[1].joinpath("seeder.py").read_text(
        encoding="utf-8"
    )
    assert "delete when GS1/GS3/GS4 land" in text


def test_fold_status_event_type_and_call_site() -> None:
    assert signals.MONITOR_SEED_FOLD_STATUS == "monitor.seed.fold_status"
    from scripts.model_manager.ui.dispatch_monitor.ulg import snapshot_events

    assert snapshot_events.fold_status_failure_event().signal == (
        "monitor.seed.fold_status"
    )
