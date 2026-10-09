"""Tests for /health VRAM selection on router-only master (federated_manager)."""

from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.federation.common.types import FederatedGateway
from systems.federation.master.manager.federated_gateway_manager import (
    FederatedGatewayManager,
)
from systems.proxy.routers.api import gateways as gateways_router
from systems.proxy.routers.health import health_check
from systems.proxy.vram_selection import select_best_vram_from_status


def _reachable_gateway(
    gateway_id: str,
    *,
    vram_free_mb: int,
    vram_total_mb: int,
    backend_type: str = "federated",
) -> FederatedGateway:
    now = time.time()
    return FederatedGateway(
        gateway_id=gateway_id,
        remote_stargate_id=f"remote-{gateway_id}",
        remote_stargate_url=f"http://{gateway_id}:9999",
        backend_type=backend_type,
        vram_free_mb=vram_free_mb,
        vram_total_mb=vram_total_mb,
        last_heartbeat=now,
        telemetry_timestamp=now,
    )


class FakeFederatedManager:
    """Minimal stand-in: registration-order gateway list."""

    def __init__(self, gateways: list[FederatedGateway]) -> None:
        self._gateways = {gw.gateway_id: gw for gw in gateways}

    def get_all_gateways(self) -> list[FederatedGateway]:
        return list(self._gateways.values())


def _proxy_with_federated(
    gateways: list[FederatedGateway],
    *,
    pipeline_ready: bool = True,
    pipeline_count: int = 3,
) -> SimpleNamespace:
    pipelines = MagicMock()
    pipelines.pipelines = [object()] * pipeline_count
    return SimpleNamespace(
        federated_manager=FakeFederatedManager(gateways),
        gateway_manager=None,
        is_pipeline_system_ready=pipeline_ready,
        pipeline_registry=pipelines,
    )


@pytest.mark.asyncio
async def test_health_vram_matches_status_full_best() -> None:
    """AC1: cloud 0/0 first, then two GPU edges — /health matches status/full best."""
    cloud = _reachable_gateway(
        "cloud-openrouter",
        vram_free_mb=0,
        vram_total_mb=0,
        backend_type="cloud_api",
    )
    edge_local = _reachable_gateway(
        "edge-localhost-gateway",
        vram_free_mb=11006,
        vram_total_mb=32607,
    )
    edge_jupiter = _reachable_gateway(
        "edge-jupiter-gateway",
        vram_free_mb=1730,
        vram_total_mb=32607,
    )
    gateways = [cloud, edge_local, edge_jupiter]
    proxy = _proxy_with_federated(gateways)

    health_payload = await health_check(
        request=MagicMock(),
        proxy=proxy,
        _current_user={},
    )

    manager = FederatedGatewayManager(event_bus=MagicMock())
    for gw in gateways:
        manager._gateways[gw.gateway_id] = gw
    status = manager.get_gateway_status_full()
    best_id, best_vram = select_best_vram_from_status(status)

    assert best_vram == 32607
    assert best_id == "edge-localhost-gateway"
    assert health_payload["vram_total_mb"] == best_vram
    assert health_payload["vram_free_mb"] == edge_local.vram_free_mb
    assert health_payload["gateways_connected"] == 3


@pytest.mark.asyncio
async def test_health_all_zero_vram_uses_first_gateway() -> None:
    """AC2a: all reachable at 0/0 — report 0/0; other fields unchanged."""
    g1 = _reachable_gateway(
        "cloud-a", vram_free_mb=0, vram_total_mb=0, backend_type="cloud_api"
    )
    g2 = _reachable_gateway(
        "cloud-b", vram_free_mb=0, vram_total_mb=0, backend_type="cloud_api"
    )
    proxy = _proxy_with_federated([g1, g2], pipeline_ready=False, pipeline_count=0)

    payload = await health_check(
        request=MagicMock(),
        proxy=proxy,
        _current_user={},
    )

    assert payload["vram_free_mb"] == 0
    assert payload["vram_total_mb"] == 0
    assert payload["gateways_connected"] == 2
    assert payload["status"] == "stargate_proxy_healthy"
    assert payload["gateway_status"] == "available"
    assert payload["pipeline_count"] == 0
    assert "code_version" in payload


@pytest.mark.asyncio
async def test_health_no_reachable_gateways_unchanged() -> None:
    """AC2b: no reachable gateway branch unchanged."""
    stale = time.time() - 120
    unreachable = FederatedGateway(
        gateway_id="edge-down",
        remote_stargate_id="remote-down",
        remote_stargate_url="http://down:9999",
        last_heartbeat=stale,
        telemetry_timestamp=stale,
    )
    proxy = _proxy_with_federated([unreachable])

    payload = await health_check(
        request=MagicMock(),
        proxy=proxy,
        _current_user={},
    )

    assert payload["gateway_status"] == "unavailable"
    assert payload["status"] == "stargate_proxy_healthy"
    assert "vram_free_mb" not in payload
    assert payload["message"] == "Stargate proxy is running but no gateway connected"


@pytest.mark.asyncio
async def test_gateways_connected_is_reachable_count() -> None:
    """AC2c: gateways_connected equals reachable count only."""
    ok = _reachable_gateway("edge-ok", vram_free_mb=100, vram_total_mb=1000)
    stale = replace(
        _reachable_gateway("edge-stale", vram_free_mb=200, vram_total_mb=2000),
        last_heartbeat=time.time() - 120,
        telemetry_timestamp=time.time() - 120,
    )
    proxy = _proxy_with_federated([ok, stale])

    payload = await health_check(
        request=MagicMock(),
        proxy=proxy,
        _current_user={},
    )

    assert payload["gateways_connected"] == 1
    assert payload["vram_total_mb"] == 1000


def _relay_proxy(
    *,
    connected: bool,
    pipeline_ready: bool = True,
    pipeline_count: int = 2,
) -> SimpleNamespace:
    pipelines = MagicMock()
    pipelines.pipelines = [object()] * pipeline_count
    proxy = SimpleNamespace(
        federated_manager=None,
        gateway_manager=None,
        is_pipeline_system_ready=pipeline_ready,
        pipeline_registry=pipelines,
        federation_local_edge_client=SimpleNamespace(is_connected=connected),
    )

    async def _forward_request(**_kwargs):
        raise AssertionError("relay health must not fall through to forward_request")

    proxy.forward_request = _forward_request
    return proxy


@pytest.mark.asyncio
async def test_health_relay_local_edge_connected_reports_available() -> None:
    """Relay with an authenticated local edge reports the gateway available."""
    ready = await health_check(
        request=MagicMock(),
        proxy=_relay_proxy(connected=True, pipeline_ready=True),
        _current_user={},
    )
    assert ready["status"] == "healthy"
    assert ready["gateway_status"] == "available"
    assert ready["gateways_connected"] == 1
    assert ready["pipeline_system_ready"] is True
    assert ready["pipeline_count"] == 2
    assert ready["version"] == "1.0.0"
    assert "code_version" in ready

    not_ready = await health_check(
        request=MagicMock(),
        proxy=_relay_proxy(connected=True, pipeline_ready=False, pipeline_count=0),
        _current_user={},
    )
    assert not_ready["status"] == "stargate_proxy_healthy"
    assert not_ready["gateway_status"] == "available"
    assert not_ready["gateways_connected"] == 1
    assert not_ready["message"] == (
        "Stargate proxy is running but pipeline execution is not ready yet"
    )


@pytest.mark.asyncio
async def test_health_relay_local_edge_disconnected_reports_unavailable() -> None:
    """A present but unauthenticated local edge is unavailable, not a forward error."""
    payload = await health_check(
        request=MagicMock(),
        proxy=_relay_proxy(connected=False),
        _current_user={},
    )
    assert payload["gateway_status"] == "unavailable"
    assert payload["status"] == "stargate_proxy_healthy"
    assert payload["message"] == (
        "Stargate proxy is running but local edge is not connected"
    )
    assert "temporarily unavailable" not in payload["message"]


def test_remote_and_master_local_edge_client_accessors() -> None:
    """Remote exposes its client; a master-like mode integration does not."""
    from systems.federation.integration.core import FederationIntegration
    from systems.federation.remote.integration import RemoteIntegration

    client = object()
    remote = RemoteIntegration.__new__(RemoteIntegration)
    remote._local_edge_client = client
    assert remote.local_edge_client is client

    federation = FederationIntegration.__new__(FederationIntegration)
    federation._mode_integration = SimpleNamespace(federated_manager=object())
    assert federation.local_edge_client is None


def test_status_full_best_vram_helper() -> None:
    """status/full route uses the same selection helper (regression guard)."""
    status = {
        "cloud": {"enabled": True, "is_connected": True, "total_vram_mb": 0},
        "edge": {"enabled": True, "is_connected": True, "total_vram_mb": 32607},
    }
    assert select_best_vram_from_status(status) == ("edge", 32607)


def test_gateway_status_full_endpoint_summary() -> None:
    """Exercise gateways/status/full handler with federated_manager mock."""
    cloud = _reachable_gateway(
        "cloud-openrouter",
        vram_free_mb=0,
        vram_total_mb=0,
        backend_type="cloud_api",
    )
    edge = _reachable_gateway(
        "edge-localhost-gateway",
        vram_free_mb=11006,
        vram_total_mb=32607,
    )

    class Manager:
        def get_gateway_status_full(self):
            fm = FederatedGatewayManager(event_bus=MagicMock())
            fm._gateways = {
                cloud.gateway_id: cloud,
                edge.gateway_id: edge,
            }
            return fm.get_gateway_status_full()

    proxy = SimpleNamespace(
        gateway_manager=None,
        federated_manager=Manager(),
    )

    app = FastAPI()
    app.dependency_overrides[gateways_router.get_proxy] = lambda: proxy
    app.dependency_overrides[gateways_router.get_auth_dependency] = lambda: {}
    app.include_router(gateways_router.router, prefix="/api/v1")

    client = TestClient(app)
    response = client.get("/api/v1/gateways/status/full")
    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["best_vram_mb"] == 32607
    assert summary["best_vram_gateway"] == "edge-localhost-gateway"
