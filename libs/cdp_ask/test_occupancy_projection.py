"""Hermetic tests for the asynchronous CDP occupancy projection."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.occupancy_projection import (
    CdpOccupancyProjection,
    occupancy_freshness_ttl_s,
    occupancy_interval_s,
)

pytestmark = pytest.mark.offline


def _ports(live_count: int) -> list[Any]:
    return [
        SimpleNamespace(has_live_cse=index < live_count)
        for index in range(live_count + 1)
    ]


def test_occupancy_tuning_defaults_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CDP_OCCUPANCY_INTERVAL_S", "4")
    monkeypatch.setenv("CDP_OCCUPANCY_FRESHNESS_TTL_S", "12")
    assert occupancy_interval_s() == 4.0
    assert occupancy_freshness_ttl_s() == 12.0
    monkeypatch.setenv("CDP_OCCUPANCY_INTERVAL_S", "invalid")
    monkeypatch.setenv("CDP_OCCUPANCY_FRESHNESS_TTL_S", "0")
    assert occupancy_interval_s() == 30.0
    assert occupancy_freshness_ttl_s() == 90.0


@pytest.mark.asyncio
async def test_refresh_records_projection_and_deduplicates_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[Any] = []
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_events.emit",
        emitted.append,
    )
    ports = _ports(2)
    projection = CdpOccupancyProjection(
        probe=lambda: ports,
        capacity_probe=lambda: 3,
        freshness_ttl_s=10,
    )

    first = await projection.refresh()
    await projection.refresh()
    assert first["live_cse_count"] == 2
    assert first["registry_capacity_count"] == 3
    assert first["freshness"] == "fresh"
    assert len(emitted) == 1
    assert emitted[0].signal == "cdp.occupancy.updated"

    ports[:] = _ports(1)
    await projection.refresh()
    assert len(emitted) == 2
    assert projection.snapshot()["live_cse_count"] == 1


def test_stale_and_unobserved_occupancy_fail_closed() -> None:
    projection = CdpOccupancyProjection(
        probe=lambda: [],
        capacity_probe=lambda: 0,
        freshness_ttl_s=1,
    )
    assert projection.snapshot(now=100)["freshness"] == "unobserved"
    assert projection.safe_busy(0, now=100)

    projection.record_observation(0, 0, observed_at=100)
    assert projection.snapshot(now=100.5)["freshness"] == "fresh"
    assert projection.safe_busy(0, now=100.5) is False
    assert projection.snapshot(now=102)["freshness"] == "stale"
    assert projection.safe_busy(0, now=102)


@pytest.mark.asyncio
async def test_refresh_is_single_flight_when_called_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def probe() -> list[Any]:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        started.set()
        release.wait(timeout=2)
        with lock:
            active -= 1
        return []

    monkeypatch.setattr("claude_bundles.cdp_registry_events.emit", lambda _: None)
    projection = CdpOccupancyProjection(
        probe=probe,
        capacity_probe=lambda: 0,
        freshness_ttl_s=10,
    )
    first = asyncio.create_task(projection.refresh())
    await asyncio.to_thread(started.wait, 1)
    second = asyncio.create_task(projection.refresh())
    await asyncio.sleep(0.02)
    assert maximum == 1
    release.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_active_work_remains_responsive_during_slow_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_probe() -> list[Any]:
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr("claude_bundles.cdp_registry_events.emit", lambda _: None)
    projection = CdpOccupancyProjection(
        interval_s=60,
        probe=slow_probe,
        capacity_probe=lambda: 0,
    )
    store = ExecutionStore()
    store.bind_occupancy(projection)
    await projection.start()
    await asyncio.to_thread(started.wait, 1)

    snapshot = await asyncio.wait_for(store.active_work_snapshot(), timeout=0.05)
    assert snapshot["running_count"] == 0
    assert "live_cse_count" not in snapshot

    release.set()
    await projection.stop()


@pytest.mark.asyncio
async def test_registry_transition_wakes_background_sensor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cdp_registry import Registration

    calls = {"n": 0}

    def probe() -> list[Any]:
        calls["n"] += 1
        return []

    monkeypatch.setattr(
        "claude_bundles.cdp_registry_events._mirror_to_event_service",
        lambda _: None,
    )
    projection = CdpOccupancyProjection(
        interval_s=60,
        probe=probe,
        capacity_probe=lambda: 0,
    )
    await projection.start()
    await asyncio.sleep(0.03)
    initial_calls = calls["n"]
    assert initial_calls >= 1

    reg = Registration(
        registration_id="wake-reg",
        port=9223,
        profile_suffix="reg-wake",
        profile=Path("/tmp/profile"),
        cdp_url="http://127.0.0.1:9223",
        holder="test",
    )
    from claude_bundles import cdp_registry_events

    cdp_registry_events.emit(cdp_registry_events.cdp_port_registered(reg))
    await asyncio.sleep(0.03)
    await projection.stop()
    assert calls["n"] > initial_calls


@pytest.mark.asyncio
async def test_drain_snapshot_uses_cached_projection_and_fails_closed_without_one() -> None:
    bare_store = ExecutionStore()
    bare = await bare_store.drain_state_snapshot()
    assert bare["busy"] is True
    assert bare["occupancy_freshness"] == "unobserved"

    projection = CdpOccupancyProjection(
        probe=lambda: [],
        capacity_probe=lambda: 0,
        freshness_ttl_s=10,
    )
    projection.record_observation(0, 0)
    store = ExecutionStore()
    store.bind_occupancy(projection)
    drain = await store.drain_state_snapshot()
    assert drain["busy"] is False
    assert drain["live_cse_count"] == 0
    assert drain["live_cse_count_authority"] == "observed"
    assert drain["occupancy_freshness"] == "fresh"


def test_drain_endpoint_exposes_projection_without_census_in_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: tmp_path)
    monkeypatch.setattr("claude_bundles.cdp_registry.list_active", lambda: [])
    monkeypatch.setattr("claude_bundles.cdp_registry.count_capacity_lanes", lambda: 0)
    monkeypatch.setattr("claude_bundles.cdp_orphans.probe_live_ports", lambda: [])
    monkeypatch.setattr("claude_bundles.cdp_registry_events.emit", lambda _: None)
    app = create_app(store=ExecutionStore())

    with TestClient(app) as client:
        app.state.occupancy.record_observation(0, 0)
        response = client.get("/v1/project-ask/drain-state")

    assert response.status_code == 200
    data = response.json()
    assert data["busy"] is False
    assert data["live_cse_count"] == 0
    assert data["occupancy_freshness"] == "fresh"
