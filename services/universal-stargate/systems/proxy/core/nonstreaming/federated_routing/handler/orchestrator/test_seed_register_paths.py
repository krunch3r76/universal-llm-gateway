"""Slice 2 tests 2.15–2.16: register and backfill keep catalog seeding."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.federation.master.manager.federated_gateway_manager import (  # noqa: E402
    FederatedGatewayManager,
)
from systems.routing.capacity.pool import CapacityPool  # noqa: E402

M = ModelId.parse("qwen3-5-9b-q8-0-131072")
GW = "cloud-proxy-gateway"


def _cloud_gateway() -> FederatedGateway:
    return FederatedGateway(
        gateway_id=GW,
        remote_stargate_url="http://cloud",
        remote_stargate_id="cloud-remote",
        model_resources={M: {"max_concurrent_requests": 8}},
        loaded_models=frozenset(),
        available_models=frozenset({M}),
    )


@pytest.mark.asyncio
async def test_2_15_register_cloud_gateway_seeds_catalog_capacity() -> None:
    pool = CapacityPool(event_bus=None)
    mgr = FederatedGatewayManager(event_bus=None, capacity_pool=pool)
    gw = _cloud_gateway()
    await mgr.register_cloud_gateway(gw)
    _a, _i, cap = pool.get_slot_info(GW, M.routing_key)
    assert cap == 8
    await mgr.stop()


def test_2_16_set_capacity_pool_backfill_seeds_unloaded_catalog() -> None:
    pool = CapacityPool(event_bus=None)
    mgr = FederatedGatewayManager(event_bus=None, capacity_pool=pool)
    mgr._gateways[GW] = _cloud_gateway()
    mgr.set_capacity_pool(pool)
    _a, _i, cap = pool.get_slot_info(GW, M.routing_key)
    assert cap == 8
