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
    )


@pytest.mark.asyncio
@patch.object(model_capacity_routes, "get_model_source_map", return_value={})
@patch.object(model_capacity_routes, "get_all_available_models", return_value={_MODEL: {}})
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
@patch.object(model_capacity_routes, "get_all_available_models", return_value={_MODEL: {}})
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
