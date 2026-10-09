"""Guards for /gateways/status and /gateways/distribution when managers are absent.

Router-only master sets gateway_manager to None and keeps federated_manager.
Both endpoints used to call gateway_manager unconditionally and raise.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.routers.api import gateways as gateways_router

_STATUS_ROWS = {
    "edge-a": {
        "enabled": True,
        "is_connected": True,
        "models": ["shared", "only-a"],
    },
    "edge-b": {
        "enabled": False,
        "is_connected": False,
        "models": ["shared"],
    },
}


class _FederatedStub:
    def __init__(self) -> None:
        self.calls = 0

    def get_gateway_status_full(self):
        self.calls += 1
        return _STATUS_ROWS


class _GatewayManagerStub:
    def __init__(self) -> None:
        self.status_calls = 0
        self.distribution_calls = 0

    def get_gateway_status(self):
        self.status_calls += 1
        return {
            "local": {"enabled": True, "is_connected": True},
        }

    def get_model_distribution(self):
        self.distribution_calls += 1
        return {"local-only": ["local"]}


class _BoomFederated:
    def get_gateway_status_full(self):
        raise AssertionError("federated_manager must stay unused")


def _client(proxy: SimpleNamespace) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[gateways_router.get_proxy] = lambda: proxy
    app.dependency_overrides[gateways_router.get_auth_dependency] = lambda: {}
    app.include_router(gateways_router.router, prefix="/api/v1")
    return TestClient(app)


def test_status_router_only_uses_federated_summary() -> None:
    federated = _FederatedStub()
    client = _client(SimpleNamespace(gateway_manager=None, federated_manager=federated))
    response = client.get("/api/v1/gateways/status")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total_gateways": 2,
        "enabled_gateways": 1,
        "disabled_gateways": 1,
        "connected_gateways": 1,
        "disconnected_gateways": 1,
    }
    assert set(body["gateways"]) == {"edge-a", "edge-b"}
    assert federated.calls == 1


def test_status_both_managers_absent_returns_empty() -> None:
    client = _client(SimpleNamespace(gateway_manager=None, federated_manager=None))
    response = client.get("/api/v1/gateways/status")
    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "total_gateways": 0,
            "enabled_gateways": 0,
            "disabled_gateways": 0,
            "connected_gateways": 0,
            "disconnected_gateways": 0,
        },
        "gateways": {},
    }


def test_status_gateway_manager_path_unchanged() -> None:
    manager = _GatewayManagerStub()
    client = _client(
        SimpleNamespace(gateway_manager=manager, federated_manager=_BoomFederated())
    )
    response = client.get("/api/v1/gateways/status")
    assert response.status_code == 200
    assert response.json()["summary"]["total_gateways"] == 1
    assert response.json()["summary"]["enabled_gateways"] == 1
    assert response.json()["gateways"]["local"]["is_connected"] is True
    assert manager.status_calls == 1


def test_distribution_router_only_uses_federated_models() -> None:
    federated = _FederatedStub()
    client = _client(SimpleNamespace(gateway_manager=None, federated_manager=federated))
    response = client.get("/api/v1/gateways/distribution")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total_models": 2,
        "models_on_multiple_gateways": 1,
        "models_on_single_gateway": 1,
    }
    assert body["distribution"]["shared"] == ["edge-a", "edge-b"]
    assert body["distribution"]["only-a"] == ["edge-a"]
    assert federated.calls == 1


def test_distribution_both_managers_absent_returns_empty() -> None:
    client = _client(SimpleNamespace(gateway_manager=None, federated_manager=None))
    response = client.get("/api/v1/gateways/distribution")
    assert response.status_code == 200
    assert response.json() == {
        "summary": {
            "total_models": 0,
            "models_on_multiple_gateways": 0,
            "models_on_single_gateway": 0,
        },
        "distribution": {},
    }


def test_distribution_gateway_manager_path_unchanged() -> None:
    manager = _GatewayManagerStub()
    client = _client(
        SimpleNamespace(gateway_manager=manager, federated_manager=_BoomFederated())
    )
    response = client.get("/api/v1/gateways/distribution")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_models"] == 1
    assert body["summary"]["models_on_single_gateway"] == 1
    assert body["distribution"] == {"local-only": ["local"]}
    assert manager.distribution_calls == 1
