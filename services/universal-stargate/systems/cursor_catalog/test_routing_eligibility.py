"""Slice-2 routing eligibility tests for cursor catalog gateways."""

from __future__ import annotations

from model_id import ModelId

from systems.federation.common.types import FederatedGateway
from systems.routing.selection.stargate_collector import (
    federated_gateways_to_routing_candidates,
)


def test_dispatchable_false_gateways_excluded_from_routing_candidates() -> None:
    catalog_gateway = FederatedGateway(
        gateway_id="cursor-sdk-catalog",
        remote_stargate_id="cursor-sdk",
        remote_stargate_url="http://127.0.0.1:8091",
        backend_type="cursor_sdk",
        available_models=frozenset({ModelId.parse("cursor/composer-2.5")}),
        dispatchable=False,
    )
    cloud_gateway = FederatedGateway(
        gateway_id="cloud-openai",
        remote_stargate_id="cloud-openai",
        remote_stargate_url="http://127.0.0.1:9999",
        backend_type="cloud_api",
        available_models=frozenset({ModelId.parse("openai/gpt-5.5")}),
        dispatchable=True,
    )

    candidates = federated_gateways_to_routing_candidates(
        [catalog_gateway, cloud_gateway]
    )

    assert [candidate.name for candidate in candidates] == ["cloud-openai"]
