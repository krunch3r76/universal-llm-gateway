"""Tests for simplified multi-gateway routing logic."""

from unittest.mock import AsyncMock, Mock

import pytest

from systems.proxy.core.resource_aware_model_manager import ResourceAwareModelManager
from systems.proxy.stargate_config.config import StargateConfig


class FakeResponse:
    """Minimal response stub exposing a json() helper."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class MockGatewayInstance:
    """Lightweight stand-in for GatewayInstance."""

    def __init__(self, name: str, url: str, is_healthy: bool = True, average_response_time: float = 0.1):
        self.config = Mock()
        self.config.name = name
        self.config.base_url = url
        self.is_healthy = is_healthy
        self.average_response_time = average_response_time
        self._http_client = AsyncMock()

    def get_http_client(self):
        return self._http_client

    def record_request_time(self, duration: float, success: bool = True):
        # keep behaviour similar to real GatewayInstance for sorting to remain deterministic
        self.average_response_time = duration


class MockMultiGatewayManager:
    def __init__(self, gateways: list[MockGatewayInstance]):
        self._gateways = gateways

    def get_healthy_gateways(self):
        return [gw for gw in self._gateways if gw.is_healthy]

    def get_gateway_by_name(self, name: str):
        for gateway in self._gateways:
            if gateway.config.name == name:
                return gateway
        return None


@pytest.fixture
def mock_config():
    cfg = Mock(spec=StargateConfig)
    return cfg


@pytest.fixture
def gateway_a():
    return MockGatewayInstance("gateway-a", "http://gw-a")


@pytest.fixture
def gateway_b():
    return MockGatewayInstance("gateway-b", "http://gw-b", average_response_time=0.2)


@pytest.fixture
def gateway_manager(gateway_a, gateway_b):
    return MockMultiGatewayManager([gateway_a, gateway_b])


@pytest.fixture
def manager(gateway_manager, mock_config):
    return ResourceAwareModelManager(gateway_manager, mock_config)


@pytest.mark.asyncio
async def test_prefers_requested_gateway_when_healthy(manager, gateway_a, gateway_b):
    gateway_a.get_http_client().get.return_value = FakeResponse({"status": "loaded"})

    selected = await manager.ensure_model_loaded("test-model", preferred_gateway="gateway-a")

    assert selected is gateway_a
    gateway_a.get_http_client().post.assert_not_awaited()


@pytest.mark.asyncio
async def test_triggers_model_load_when_not_ready(manager, gateway_a):
    # first poll returns unloaded, subsequent poll returns loaded
    gateway_a.get_http_client().get.side_effect = [
        FakeResponse({"status": "unloaded"}),
        FakeResponse({"status": "loaded"}),
    ]

    selected = await manager.ensure_model_loaded("warm-me-up")

    assert selected is gateway_a
    assert gateway_a.get_http_client().post.await_count == 1


@pytest.mark.asyncio
async def test_falls_back_to_next_gateway_when_first_fails(manager, gateway_a, gateway_b):
    # first gateway fails to load
    gateway_a.get_http_client().get.return_value = FakeResponse({"status": "unloaded"})
    gateway_a.get_http_client().post.side_effect = Exception("load failed")

    gateway_b.get_http_client().get.side_effect = [
        FakeResponse({"status": "unloaded"}),
        FakeResponse({"status": "loaded"}),
    ]

    selected = await manager.ensure_model_loaded("fallback-model")

    assert selected is gateway_b
    assert gateway_b.get_http_client().post.await_count == 1
