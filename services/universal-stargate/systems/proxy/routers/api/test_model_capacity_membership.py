"""GET /model-capacity gateway membership (federated loaded_on ids)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from systems.federation.common.types import FederatedGateway
from systems.proxy.routers.api import model_capacity as model_capacity_routes

_MODEL = "qwen3-14b-q4-k-m"


def _proxy_with_gateways(gateways: list[FederatedGateway]) -> SimpleNamespace:
    fm = MagicMock()
    fm.get_all_gateways.return_value = gateways
    return SimpleNamespace(federated_manager=fm, gateway_manager=MagicMock())


def _federated_gateway(*, parallel_slots: int = 2) -> FederatedGateway:
    return FederatedGateway(
        gateway_id="edge-jupiter-gateway",
        node_id="jupiter",
        remote_stargate_id="relay-jupiter",
        remote_stargate_url="http://relay-jupiter:9999",
        model_resources={_MODEL: {"parallel_slots": parallel_slots}},
        loaded_models=frozenset({_MODEL}),
    )


@pytest.mark.asyncio
@patch.object(model_capacity_routes, "get_model_source_map", return_value={})
@patch.object(
    model_capacity_routes,
    "get_all_available_models",
    return_value={_MODEL: {}},
)
@patch.object(model_capacity_routes, "get_model_status_map")
@patch.object(model_capacity_routes, "get_federation_integration")
async def test_get_model_capacity_federated_total_slots(
    mock_federation: MagicMock,
    mock_status_map: MagicMock,
    _all_models: MagicMock,
    _source_map: MagicMock,
) -> None:
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["relay-jupiter"], "busy_on": []},
    }
    proxy = _proxy_with_gateways([_federated_gateway(parallel_slots=2)])

    payload = await model_capacity_routes.get_model_capacity(
        _MODEL, proxy=proxy, _current_user={}
    )

    assert payload["total_parallel_slots"] == 2
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["parallel_slots"] == 2


@pytest.mark.asyncio
@patch.object(model_capacity_routes, "get_model_source_map", return_value={})
@patch.object(
    model_capacity_routes,
    "get_all_available_models",
    return_value={_MODEL: {}},
)
@patch.object(model_capacity_routes, "get_model_status_map")
@patch.object(model_capacity_routes, "get_federation_integration")
async def test_get_model_capacity_empty_active_set(
    mock_federation: MagicMock,
    mock_status_map: MagicMock,
    _all_models: MagicMock,
    _source_map: MagicMock,
) -> None:
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {_MODEL: {"loaded_on": [], "busy_on": []}}
    proxy = _proxy_with_gateways([_federated_gateway()])

    payload = await model_capacity_routes.get_model_capacity(
        _MODEL, proxy=proxy, _current_user={}
    )

    assert payload["total_parallel_slots"] == 0
    assert payload["nodes"] == []


@pytest.mark.asyncio
@patch.object(model_capacity_routes, "get_model_source_map", return_value={})
@patch.object(
    model_capacity_routes,
    "get_all_available_models",
    return_value={_MODEL: {}},
)
@patch.object(model_capacity_routes, "get_model_status_map")
@patch.object(model_capacity_routes, "get_federation_integration")
async def test_federated_busy_status_uses_remote_stargate_id(
    mock_federation: MagicMock,
    mock_status_map: MagicMock,
    _all_models: MagicMock,
    _source_map: MagicMock,
) -> None:
    """Busy federated rows key busy_on by remote_stargate_id, not node_id."""
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["relay-jupiter"], "busy_on": ["relay-jupiter"]},
    }
    gateway = FederatedGateway(
        gateway_id="edge-jupiter-gateway",
        node_id="jupiter",
        remote_stargate_id="relay-jupiter",
        remote_stargate_url="http://relay-jupiter:9999",
        model_resources={_MODEL: {"parallel_slots": 2}},
        loaded_models=frozenset({_MODEL}),
        busy_models=frozenset({_MODEL}),
    )
    proxy = _proxy_with_gateways([gateway])

    payload = await model_capacity_routes.get_model_capacity(
        _MODEL, proxy=proxy, _current_user={}
    )

    assert payload["total_parallel_slots"] == 2
    assert payload["nodes"] == [
        {"node_id": "jupiter", "parallel_slots": 2, "status": "busy"}
    ]


@pytest.mark.asyncio
@patch.object(model_capacity_routes, "get_model_source_map", return_value={})
@patch.object(
    model_capacity_routes,
    "get_all_available_models",
    return_value={_MODEL: {}},
)
@patch.object(model_capacity_routes, "get_model_status_map")
@patch.object(model_capacity_routes, "get_federation_integration")
async def test_get_model_capacity_same_relay_sibling_not_double_counted(
    mock_federation: MagicMock,
    mock_status_map: MagicMock,
    _all_models: MagicMock,
    _source_map: MagicMock,
) -> None:
    """A sibling that only has the model installed does not add slots."""
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["relay-jupiter"], "busy_on": []},
    }
    loaded = FederatedGateway(
        gateway_id="edge-jupiter-a",
        node_id="jupiter-a",
        remote_stargate_id="relay-jupiter",
        remote_stargate_url="http://relay-jupiter:9999",
        model_resources={_MODEL: {"parallel_slots": 2}},
        loaded_models=frozenset({_MODEL}),
    )
    installed_only = FederatedGateway(
        gateway_id="edge-jupiter-b",
        node_id="jupiter-b",
        remote_stargate_id="relay-jupiter",
        remote_stargate_url="http://relay-jupiter:9999",
        model_resources={_MODEL: {"parallel_slots": 5}},
    )
    proxy = _proxy_with_gateways([loaded, installed_only])

    payload = await model_capacity_routes.get_model_capacity(
        _MODEL, proxy=proxy, _current_user={}
    )

    assert payload["total_parallel_slots"] == 2
    assert payload["nodes"] == [
        {"node_id": "jupiter-a", "parallel_slots": 2, "status": "loaded"}
    ]
