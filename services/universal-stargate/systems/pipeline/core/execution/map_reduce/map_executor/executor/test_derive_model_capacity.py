"""Membership tests for derive_model_capacity (federated loaded_on ids)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from systems.federation.common.types import FederatedGateway
from systems.pipeline.core.execution.map_reduce.map_executor.executor.capacity import (
    derive_model_capacity,
)

_MODEL = "qwen3-14b-q4-k-m"


def _executor_with_gateways(gateways: list[FederatedGateway]) -> SimpleNamespace:
    fm = MagicMock()
    fm.get_all_gateways.return_value = gateways
    proxy = SimpleNamespace(federated_manager=fm, gateway_manager=MagicMock())
    return SimpleNamespace(_runtime=SimpleNamespace(_proxy=proxy))


def _federated_gateway(*, parallel_slots: int = 2) -> FederatedGateway:
    return FederatedGateway(
        gateway_id="edge-jupiter-gateway",
        node_id="jupiter",
        remote_stargate_id="relay-jupiter",
        remote_stargate_url="http://relay-jupiter:9999",
        model_resources={_MODEL: {"parallel_slots": parallel_slots}},
        loaded_models=frozenset({_MODEL}),
    )


def _local_gateway(*, parallel_slots: int = 3) -> FederatedGateway:
    return FederatedGateway(
        gateway_id="edge-localhost-gateway",
        node_id="localhost",
        remote_stargate_id="",
        remote_stargate_url="http://localhost:9999",
        model_resources={_MODEL: {"parallel_slots": parallel_slots}},
        loaded_models=frozenset({_MODEL}),
    )


@patch("systems.routing.selection.catalog.get_model_status_map")
@patch("systems.federation.get_federation_integration")
def test_federated_loaded_on_uses_remote_stargate_id(
    mock_federation: MagicMock, mock_status_map: MagicMock
) -> None:
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["relay-jupiter"], "busy_on": []},
    }
    executor = _executor_with_gateways([_federated_gateway(parallel_slots=2)])

    assert derive_model_capacity(executor, {0: _MODEL}) == 2


@patch("systems.routing.selection.catalog.get_model_status_map")
@patch("systems.federation.get_federation_integration")
def test_federated_does_not_match_node_id_in_active_set(
    mock_federation: MagicMock, mock_status_map: MagicMock
) -> None:
    """node_id in loaded_on does not count when remote_stargate_id is set."""
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["jupiter"], "busy_on": []},
    }
    executor = _executor_with_gateways([_federated_gateway(parallel_slots=2)])

    assert derive_model_capacity(executor, {0: _MODEL}) is None


@patch("systems.routing.selection.catalog.get_model_status_map")
@patch("systems.federation.get_federation_integration")
def test_empty_loaded_on_returns_none(
    mock_federation: MagicMock, mock_status_map: MagicMock
) -> None:
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="master-local")
    )
    mock_status_map.return_value = {_MODEL: {"loaded_on": [], "busy_on": []}}
    executor = _executor_with_gateways([_federated_gateway()])

    assert derive_model_capacity(executor, {0: _MODEL}) is None


@patch("systems.routing.selection.catalog.get_model_status_map")
@patch("systems.federation.get_federation_integration")
def test_local_gateway_matches_gateway_id_in_loaded_on(
    mock_federation: MagicMock, mock_status_map: MagicMock
) -> None:
    mock_federation.return_value = SimpleNamespace(
        config=SimpleNamespace(stargate_id="edge-localhost")
    )
    mock_status_map.return_value = {
        _MODEL: {"loaded_on": ["edge-localhost-gateway"], "busy_on": []},
    }
    executor = _executor_with_gateways([_local_gateway(parallel_slots=3)])

    assert derive_model_capacity(executor, {0: _MODEL}) == 3


@patch("systems.routing.selection.catalog.get_model_status_map")
@patch("systems.federation.get_federation_integration")
def test_same_relay_sibling_slots_not_double_counted(
    mock_federation: MagicMock, mock_status_map: MagicMock
) -> None:
    """Installed-only sibling on the same relay must not add parallel_slots."""
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
    executor = _executor_with_gateways([loaded, installed_only])

    assert derive_model_capacity(executor, {0: _MODEL}) == 2
