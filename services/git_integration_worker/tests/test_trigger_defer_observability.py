"""Defer accounting, fleet verdict, degraded threshold, coalesce, API surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from agent_bus_store.auth import require_token

from services.git_integration_worker.app import create_app
from services.git_integration_worker.trigger_service.config import fire_interval_s
from services.git_integration_worker.trigger_service.fleet_idle import (
    FleetIdleSnapshot,
    FleetVerdict,
    begin_idle_pass,
    read_fleet_idle_memoized,
    reset_grace_tracker,
)
from services.git_integration_worker.trigger_service.models import (
    PREDICATE_FLEET_IDLE,
    STATUS_FIRING,
    STATUS_SCHEDULED,
)
from services.git_integration_worker.trigger_service.store import TriggerStore

_PROMPT = "cortex://notes/system/threads/test-prompt.md"
_FLEET_ARGS = {
    "require_tick_empty": True,
    "require_dispatch_idle": True,
    "grace_s": 0,
}


class _StaticFleetReader:
    def __init__(self, snapshot: FleetIdleSnapshot) -> None:
        self._snapshot = snapshot

    def read(self) -> FleetIdleSnapshot:
        return self._snapshot


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriggerStore:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    reset_grace_tracker()
    begin_idle_pass()
    return TriggerStore()


def _schedule_idle(store: TriggerStore, *, fire_at: datetime, recur_every_s: int | None = None):
    return store.schedule(
        created_by="test",
        fire_at=fire_at,
        prompt_uri=_PROMPT,
        predicate=PREDICATE_FLEET_IDLE,
        predicate_args=_FLEET_ARGS,
        recur_every_s=recur_every_s,
    )


def _patch_fleet(snapshot: FleetIdleSnapshot):
    reader = _StaticFleetReader(snapshot)
    return patch(
        "services.git_integration_worker.trigger_service.store_claim.read_fleet_idle_memoized",
        side_effect=lambda _reader=None: read_fleet_idle_memoized(reader),
    )


def test_defer_increments_count_and_last_deferred_at(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    with _patch_fleet(busy):
        begin_idle_pass()
        assert store.claim_due(now=now) is None
    updated = store.get(row.id)
    assert updated is not None
    assert updated.defer_count == 1
    assert updated.last_deferred_at is not None
    assert updated.last_fleet_verdict == "busy"
    assert updated.status == STATUS_SCHEDULED


def test_defer_resets_on_successful_claim(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    with _patch_fleet(busy):
        begin_idle_pass()
        store.claim_due(now=now)
    second_now = now + timedelta(seconds=fire_interval_s() + 1)
    with _patch_fleet(idle):
        begin_idle_pass()
        claimed = store.claim_due(now=second_now)
    assert claimed is not None
    assert claimed.id == row.id
    assert claimed.status == STATUS_FIRING
    assert claimed.defer_count == 0
    assert claimed.last_deferred_at is None
    assert claimed.degraded == 0


def test_undetermined_verdict_distinct_from_busy(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    undetermined = FleetIdleSnapshot(
        verdict=FleetVerdict.UNDETERMINED,
        dispatch_idle=False,
        tick_empty=False,
        cursor_auto_idle=True,
        tick_undetermined=True,
    )
    with _patch_fleet(undetermined):
        begin_idle_pass()
        store.claim_due(now=now)
    updated = store.get(row.id)
    assert updated is not None
    assert updated.last_fleet_verdict == "undetermined"
    assert updated.defer_count == 1


def test_defer_threshold_emits_degraded_without_cancel(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    emitted: list[tuple[str, dict]] = []

    def _capture(signal: str, payload: dict) -> None:
        emitted.append((signal, payload))

    with patch.dict("os.environ", {"TRIGGER_DEFER_THRESHOLD": "2"}):
        with _patch_fleet(busy):
            begin_idle_pass()
            store.claim_due(now=now, _emit=_capture)
            second_now = now + timedelta(seconds=fire_interval_s() + 1)
            begin_idle_pass()
            store.claim_due(now=second_now, _emit=_capture)

    updated = store.get(row.id)
    assert updated is not None
    assert updated.defer_count == 2
    assert updated.degraded == 1
    assert updated.status == STATUS_SCHEDULED
    degraded_signals = [s for s, _ in emitted if s == "giw.trigger.defer_degraded"]
    assert len(degraded_signals) == 1


def test_coalesce_records_skipped_periods(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    recur_s = 1800
    row = _schedule_idle(store, fire_at=now - timedelta(minutes=5), recur_every_s=recur_s)
    idle = FleetIdleSnapshot(
        verdict=FleetVerdict.IDLE,
        dispatch_idle=True,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    with _patch_fleet(idle):
        begin_idle_pass()
        claimed = store.claim_due(now=now)
    assert claimed is not None
    fired = store.mark_fired(row.id, execution_id="exec-coalesce")
    store.mark_reconciled(row.id, terminal_status="completed")
    fired_at = datetime.fromisoformat(fired.fired_at)
    terminal_at = fired_at + timedelta(seconds=recur_s * 4)
    rearmed = store.rearm_recurring(row.id, terminal_at=terminal_at)
    assert rearmed is not None
    assert rearmed.last_coalesce_skipped == 3


def test_route_exposes_defer_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path / "cortex"))
    monkeypatch.setenv("AGENT_BUS_TOKEN", "test-token")
    monkeypatch.setenv("PROJECT_ASK_URL", "http://127.0.0.1:8770")
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)

    store = TriggerStore()
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    with _patch_fleet(busy):
        begin_idle_pass()
        store.claim_due(now=now)

    got = client.get(f"/api/v1/triggers/{row.id}")
    assert got.status_code == 200
    body = got.json()
    assert body["defer_count"] == 1
    assert body["last_deferred_at"] is not None
    assert body["last_fleet_verdict"] == "busy"
    assert body["degraded"] is False

    listed = client.get("/api/v1/triggers")
    assert listed.status_code == 200
    match = next(t for t in listed.json()["triggers"] if t["id"] == row.id)
    assert match["defer_count"] == 1
    assert match["last_fleet_verdict"] == "busy"


def test_defer_bumps_fire_at_by_interval(store: TriggerStore) -> None:
    now = datetime.now(UTC)
    row = _schedule_idle(store, fire_at=now - timedelta(seconds=5))
    busy = FleetIdleSnapshot(
        verdict=FleetVerdict.BUSY,
        dispatch_idle=False,
        tick_empty=True,
        cursor_auto_idle=True,
    )
    with _patch_fleet(busy):
        begin_idle_pass()
        store.claim_due(now=now)
    updated = store.get(row.id)
    assert updated is not None
    expected = (now + timedelta(seconds=fire_interval_s())).isoformat()
    assert updated.fire_at[:19] == expected[:19]
