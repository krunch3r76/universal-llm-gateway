"""Tests for master loading lease table and FederatedGatewayManager integration."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from model_id import ModelId

from systems.federation.common.config.schema import LocalEdgeConfig
from systems.federation.common.types import FederatedGateway
from systems.routing.selection.decision.resource_checks import _check_resources
from systems.routing.selection.types import Gateway, Placement

from .federated_gateway_manager import FederatedGatewayManager
from .loading_leases import (
    ExpireReason,
    LeaseOrigin,
    LoadingLeaseTable,
)


class _EventCapture:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish_nowait(self, event: object) -> None:
        self.published.append(event)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def model_a() -> ModelId:
    return ModelId.parse("qwen3-14b-q4-k-m-40960")


@pytest.fixture
def model_b() -> ModelId:
    return ModelId.parse("qwen3-5-9b-q8-0-131072")


@pytest.fixture
def hermes() -> ModelId:
    return ModelId.parse("hermes-test")


def test_unacked_expires_after_floor_grace() -> None:
    clock = _FakeClock()
    table = LoadingLeaseTable(load_ack_grace_s=15, clock=clock)
    mid = ModelId.parse("m1")
    table.mark("gw1", mid, LeaseOrigin.OPTIMISTIC)
    clock.advance(16)
    expired = table.expire_gateway("gw1", unacked_grace_s=15)
    assert len(expired) == 1
    assert expired[0].reason is ExpireReason.UNACKED
    assert table.loading_models_for("gw1") == frozenset()


def test_polling_grace_max_of_floor_and_twice_poll() -> None:
    grace = LoadingLeaseTable.effective_unacked_grace_s(
        floor_s=15,
        is_http_polling=True,
        telemetry_poll_interval_ms=10_000,
    )
    assert grace == 20.0


def test_remark_after_ack_does_not_reset_unacked_grace() -> None:
    clock = _FakeClock()
    table = LoadingLeaseTable(load_ack_grace_s=15, clock=clock)
    mid = ModelId.parse("m1")
    table.mark("gw1", mid, LeaseOrigin.OPTIMISTIC)
    table.ack("gw1", mid)
    clock.advance(5)
    table.mark("gw1", mid, LeaseOrigin.OPTIMISTIC)
    clock.advance(16)
    expired = table.expire_gateway("gw1", unacked_grace_s=15)
    assert expired == []


def test_polling_unacked_still_reserved_at_19s_with_poll_10s() -> None:
    clock = _FakeClock()
    table = LoadingLeaseTable(load_ack_grace_s=15, clock=clock)
    mid = ModelId.parse("m1")
    table.mark("gw1", mid, LeaseOrigin.OPTIMISTIC)
    clock.advance(19)
    expired = table.expire_gateway("gw1", unacked_grace_s=20)
    assert expired == []
    assert mid in table.loading_models_for("gw1")


def test_progress_touch_resets_idle_expiry() -> None:
    clock = _FakeClock()
    table = LoadingLeaseTable(load_idle_budget=60, clock=clock)
    mid = ModelId.parse("m1")
    table.mark("gw1", mid, LeaseOrigin.ORCHESTRATOR)
    table.ack("gw1", mid)
    for _ in range(20):
        clock.advance(15)
        table.touch_progress("gw1", mid)
    expired = table.expire_gateway("gw1", unacked_grace_s=15)
    assert expired == []
    clock.advance(61)
    expired = table.expire_gateway("gw1", unacked_grace_s=15)
    assert len(expired) == 1
    assert expired[0].reason is ExpireReason.IDLE


def test_late_started_after_expiry_creates_fresh_acked_lease() -> None:
    clock = _FakeClock()
    table = LoadingLeaseTable(load_ack_grace_s=15, clock=clock)
    mid = ModelId.parse("m1")
    table.mark("gw1", mid, LeaseOrigin.OPTIMISTIC)
    clock.advance(16)
    table.expire_gateway("gw1", unacked_grace_s=15)
    table.ack("gw1", mid)
    assert mid in table.loading_models_for("gw1")


def test_local_edge_config_uses_default_poll_interval() -> None:
    """LocalEdgeConfig in _remote_configs must not AttributeError on expire."""
    manager = FederatedGatewayManager(event_bus=MagicMock(), load_ack_grace_s=15)
    edge = LocalEdgeConfig(
        socket_path="/tmp/edge.sock",
        stargate_id="edge-local",
        api_key="k",
    )
    manager.register_remote(
        "edge-local",
        "unix:///tmp/edge.sock",
        is_http_polling=False,
        config=edge,
    )
    gw = FederatedGateway(
        gateway_id="jupiter",
        remote_stargate_id="edge-local",
        remote_stargate_url="unix:///tmp/edge.sock",
    )
    manager._gateways["jupiter"] = gw
    mid = ModelId.parse("m1")
    manager._loading_leases.mark("jupiter", mid, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)
    assert manager._telemetry_poll_interval_ms(gw) == 5000
    manager._expire_and_project("jupiter", gw)  # must not raise


@pytest.mark.asyncio
async def test_cloud_mark_returns_false() -> None:
    manager = FederatedGatewayManager(event_bus=MagicMock())
    gw = FederatedGateway(
        gateway_id="cloud-openai",
        remote_stargate_id="r1",
        remote_stargate_url="http://cloud",
        backend_type="cloud_api",
    )
    manager._gateways["cloud-openai"] = gw
    mid = ModelId.parse("gpt-test")
    assert manager.mark_loading_optimistic("cloud-openai", mid) is False
    assert mid not in manager.get_gateway("cloud-openai").loading_models


@pytest.mark.asyncio
async def test_get_all_gateways_expire_on_read() -> None:
    clock = _FakeClock()
    bus = _EventCapture()
    manager = FederatedGatewayManager(
        event_bus=bus, load_ack_grace_s=15, loading_lease_clock=clock
    )
    mid = ModelId.parse("m1")
    gw = FederatedGateway(
        gateway_id="jupiter",
        remote_stargate_id="edge1",
        remote_stargate_url="http://jupiter",
    )
    manager._gateways["jupiter"] = gw
    manager._loading_leases.mark("jupiter", mid, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)
    clock.advance(16)
    all_gw = manager.get_all_gateways()
    assert all_gw[0].loading_models == frozenset()


@pytest.mark.asyncio
async def test_http_positive_loading_ack_only() -> None:
    manager = FederatedGatewayManager(event_bus=MagicMock(), load_ack_grace_s=15)
    gw = FederatedGateway(
        gateway_id="golem",
        remote_stargate_id="remote1",
        remote_stargate_url="http://golem",
        is_http_polling=True,
    )
    manager._gateways["golem"] = gw
    manager.register_remote("remote1", "http://x", is_http_polling=True)
    mid = ModelId.parse("m1")
    manager._loading_leases.mark("golem", mid, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)

    # loaded snapshot without loading key — stays unacked
    manager._apply_http_loading_hooks(
        "golem",
        manager._gateways["golem"],
        positive_loading=None,
        loaded_models=frozenset(),
    )
    assert mid in manager.get_gateway("golem").loading_models
    lease_before_ack = manager._loading_leases._leases.get(("golem", mid))
    assert lease_before_ack is not None
    assert lease_before_ack.acked_at is None

    manager._apply_http_loading_hooks(
        "golem",
        manager._gateways["golem"],
        positive_loading=frozenset({mid}),
        loaded_models=None,
    )
    lease = manager._loading_leases._leases.get(("golem", mid))
    assert lease is not None
    assert lease.acked_at is not None


@pytest.mark.asyncio
async def test_http_loaded_snapshot_releases_lease() -> None:
    manager = FederatedGatewayManager(event_bus=MagicMock())
    mid = ModelId.parse("m1")
    gw = FederatedGateway(
        gateway_id="golem", remote_stargate_id="r1", remote_stargate_url="http://g"
    )
    manager._gateways["golem"] = gw
    manager._loading_leases.mark("golem", mid, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)
    manager._apply_http_loading_hooks(
        "golem",
        manager._gateways["golem"],
        positive_loading=None,
        loaded_models=frozenset({mid}),
    )
    assert mid not in manager.get_gateway("golem").loading_models


@pytest.mark.asyncio
async def test_ws_snapshot_releases_intersection_loaded() -> None:
    manager = FederatedGatewayManager(event_bus=MagicMock())
    mid = ModelId.parse("m1")
    gw = FederatedGateway(
        gateway_id="edge", remote_stargate_id="r1", remote_stargate_url="http://e"
    )
    manager._gateways["edge"] = gw
    manager._loading_leases.mark("edge", mid, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)
    parsed = {
        "loaded_models": frozenset({mid}),
        "busy_models": frozenset(),
        "available_models": frozenset(),
        "available_ram_mb": 0,
        "available_vram_mb": 0,
        "total_ram_mb": 0,
        "total_vram_mb": 0,
    }
    manager._apply_gateway_snapshot(gw, parsed)
    assert mid not in manager._gateways["edge"].loading_models


@pytest.mark.asyncio
async def test_expire_emits_event_once() -> None:
    bus = _EventCapture()
    clock = _FakeClock()
    manager = FederatedGatewayManager(
        event_bus=bus, load_ack_grace_s=15, loading_lease_clock=clock
    )
    mid = ModelId.parse("m1")
    gw = FederatedGateway(
        gateway_id="gw", remote_stargate_id="r1", remote_stargate_url="http://gw"
    )
    manager._gateways["gw"] = gw
    manager._loading_leases.mark("gw", mid, LeaseOrigin.OPTIMISTIC)
    clock.advance(16)
    manager.get_gateway("gw")
    await asyncio.sleep(0)
    assert len(bus.published) == 1


def test_specimen_infeasible_at_1s_admit_at_16s(
    model_a: ModelId, model_b: ModelId, hermes: ModelId
) -> None:
    clock = _FakeClock()
    manager = FederatedGatewayManager(
        event_bus=None, load_ack_grace_s=15, loading_lease_clock=clock
    )
    gw = FederatedGateway(
        gateway_id="hermes-gw",
        remote_stargate_id="r1",
        remote_stargate_url="http://hermes",
        vram_free_mb=32109,
        vram_total_mb=32607,
    )
    manager._gateways["hermes-gw"] = gw
    manager._loading_leases.mark("hermes-gw", model_a, LeaseOrigin.OPTIMISTIC)
    manager._loading_leases.mark("hermes-gw", model_b, LeaseOrigin.OPTIMISTIC)
    manager._sync_loading_models(gw)

    def req(mid: ModelId) -> tuple[int, int]:
        vram = {model_a: 15700, model_b: 15134, hermes: 28945}[mid]
        return vram, 0

    placement = Placement(model_id=hermes, ram_mb=0, vram_mb=28945, is_gpu=True)
    routing_gw = Gateway(
        ref=None,
        name="hermes-gw",
        ram_free_mb=0,
        vram_free_mb=32109,
        vram_total_mb=32607,
        loading_models=manager._loading_leases.loading_models_for("hermes-gw"),
        model_details={
            hermes: {"vram_usage": 28945, "ram_usage": 0},
        },
    )

    ok, failure = _check_resources(routing_gw, placement, req)
    assert not ok
    assert failure is not None

    clock.advance(16)
    gw_live = manager._gateways["hermes-gw"]
    manager._expire_and_project("hermes-gw", gw_live)
    routing_gw.loading_models = manager._gateways["hermes-gw"].loading_models
    ok, _failure = _check_resources(routing_gw, placement, req)
    assert ok
