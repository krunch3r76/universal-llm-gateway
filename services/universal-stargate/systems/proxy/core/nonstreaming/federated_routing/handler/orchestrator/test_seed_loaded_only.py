"""Slice 2 tests 2.1–2.2: loaded-only GATEWAY_SNAPSHOT seeding."""

from __future__ import annotations

import asyncio
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
GW = "edge-localhost-gateway"


def _gateway(*, loaded: frozenset[ModelId]) -> FederatedGateway:
    return FederatedGateway(
        gateway_id=GW,
        remote_stargate_url="http://localhost",
        remote_stargate_id="localhost-remote",
        model_resources={
            M: {"max_concurrent_requests": 4, "vram_mb": 15000},
        },
        loaded_models=loaded,
        available_models=frozenset({M}),
    )


def _snapshot_payload(*, loaded: frozenset[ModelId]) -> dict:
    """Parsed GATEWAY_SNAPSHOT. ram/vram keys are required by extract_resource_state."""
    return {
        "available_ram_mb": 100_000,
        "available_vram_mb": 20_000,
        "total_ram_mb": 100_000,
        "total_vram_mb": 80_000,
        "loaded_models": loaded,
        "busy_models": frozenset(),
        "available_models": frozenset({M}),
        "model_resources": {M: {"max_concurrent_requests": 4, "vram_mb": 15000}},
        "activated_models": None,
    }


def _apply_snapshot(
    mgr: FederatedGatewayManager, gw: FederatedGateway, *, loaded: frozenset[ModelId]
) -> FederatedGateway:
    mgr._gateways[GW] = gw
    updated = mgr._apply_gateway_snapshot(gw, _snapshot_payload(loaded=loaded))
    return updated


@pytest.fixture
def manager_with_pool() -> FederatedGatewayManager:
    pool = CapacityPool(event_bus=None)
    pool.set_capacity(GW, M.routing_key, 1)
    mgr = FederatedGatewayManager(event_bus=None, capacity_pool=pool)
    return mgr


def test_2_1_snapshot_skips_unloaded_model_resources(
    manager_with_pool: FederatedGatewayManager,
) -> None:
    pool = manager_with_pool._capacity_pool
    assert pool is not None
    gw = _gateway(loaded=frozenset())
    _apply_snapshot(manager_with_pool, gw, loaded=frozenset())
    _avail, _in_flight, cap = pool.get_slot_info(GW, M.routing_key)
    assert cap == 1


@pytest.mark.asyncio
async def test_2_2_snapshot_seeds_loaded_model_to_catalog(
    manager_with_pool: FederatedGatewayManager,
) -> None:
    pool = manager_with_pool._capacity_pool
    assert pool is not None
    gw = _gateway(loaded=frozenset({M}))
    _apply_snapshot(manager_with_pool, gw, loaded=frozenset({M}))
    await asyncio.sleep(0)
    _avail, _in_flight, cap = pool.get_slot_info(GW, M.routing_key)
    assert cap == 4
