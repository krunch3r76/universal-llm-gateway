"""Slice 2 tests 2.10–2.14: cold-slot reset with quiet period."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[3])
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


class _Clock:
    """monotonic() clock the cold-slot tests advance by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def _install_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Patch pool mixin clocks without touching the asyncio loop clock."""
    from systems.routing.capacity._pool import (
        _acquisition,
        _bookkeeping,
        _release_dispatch,
    )

    clock = _Clock()
    monkeypatch.setattr(_acquisition, "time", clock)
    monkeypatch.setattr(_release_dispatch, "time", clock)
    monkeypatch.setattr(_bookkeeping, "time", clock)
    return clock


def _manager(pool: CapacityPool) -> FederatedGatewayManager:
    return FederatedGatewayManager(event_bus=None, capacity_pool=pool)


def _gw(*, loading: frozenset[ModelId] | None = None) -> FederatedGateway:
    return FederatedGateway(
        gateway_id=GW,
        remote_stargate_url="http://localhost",
        remote_stargate_id="localhost-remote",
        model_resources={M: {"max_concurrent_requests": 4}},
        loaded_models=frozenset(),
        loading_models=loading or frozenset(),
        available_models=frozenset({M}),
    )


def _snapshot_payload() -> dict:
    """GATEWAY_SNAPSHOT body ``extract_resource_state`` indexes by key."""
    return {
        "available_ram_mb": 100_000,
        "available_vram_mb": 20_000,
        "total_ram_mb": 100_000,
        "total_vram_mb": 80_000,
        "loaded_models": frozenset(),
        "busy_models": frozenset(),
        "available_models": frozenset({M}),
        "model_resources": {M: {"max_concurrent_requests": 4}},
        "activated_models": None,
    }


def _apply_cold_snapshot(
    mgr: FederatedGatewayManager,
    gw: FederatedGateway,
    *,
    loaded: frozenset[ModelId] = frozenset(),
) -> FederatedGateway:
    mgr._gateways[gw.gateway_id] = gw
    payload = _snapshot_payload()
    payload["loaded_models"] = loaded
    return mgr._apply_gateway_snapshot(gw, payload)


def _apply_two_cold_snapshots(
    mgr: FederatedGatewayManager, gw: FederatedGateway
) -> FederatedGateway:
    gw = _apply_cold_snapshot(mgr, gw)
    return _apply_cold_snapshot(mgr, gw)


async def _hold(pool: CapacityPool, count: int) -> list:
    tokens = []
    for i in range(count):
        tokens.append(
            await pool.acquire_token(
                request_id=f"req-hold-{i}",
                model_id=M.routing_key,
                allowed_gateway_ids=frozenset({GW}),
            )
        )
    return tokens


@pytest.fixture
def pool() -> CapacityPool:
    p = CapacityPool(event_bus=None)
    p.set_capacity(GW, M.routing_key, 4)
    return p


@pytest.mark.asyncio
async def test_2_10_two_snapshots_quiet_resets_in_flight(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw())
    await asyncio.sleep(0)
    _, in_flight, cap = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 0
    assert cap == 1


@pytest.mark.asyncio
async def test_2_11_single_snapshot_keeps_in_flight(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    _apply_cold_snapshot(mgr, _gw())
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 2


@pytest.mark.asyncio
async def test_2_12_optimistic_loading_mark_blocks_reset(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw(loading=frozenset({M})))
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 2


@pytest.mark.asyncio
async def test_2_13_short_quiet_period_blocks_reset(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 5.0
    _apply_two_cold_snapshots(mgr, _gw())
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 2


@pytest.mark.asyncio
async def test_2_14_reset_then_one_immediate_token(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw())
    await asyncio.sleep(0)
    tasks = [
        asyncio.create_task(
            pool.acquire_token(
                request_id=f"req-{i}",
                model_id=M.routing_key,
                allowed_gateway_ids=frozenset({GW}),
            )
        )
        for i in range(4)
    ]
    await asyncio.sleep(0.05)
    immediate = sum(1 for t in tasks if t.done())
    assert immediate == 1
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for t in tasks:
        if t.done() and not t.cancelled() and not t.exception():
            await t.result().release()


@pytest.mark.asyncio
async def test_2_10_reset_dispatches_queued_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_clock(monkeypatch)
    pool = CapacityPool(event_bus=None)
    pool.set_capacity(GW, M.routing_key, 1)
    mgr = _manager(pool)
    await pool.acquire_token(
        request_id="req-leader",
        model_id=M.routing_key,
        allowed_gateway_ids=frozenset({GW}),
    )
    follower = asyncio.create_task(
        pool.acquire_token(
            request_id="req-follower",
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )
    )
    await asyncio.sleep(0.02)
    assert not follower.done()
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw())
    token = await asyncio.wait_for(follower, 1.0)
    assert token.gateway_id == GW
    await token.release()


@pytest.mark.asyncio
async def test_2_13_release_refreshes_quiet_clock(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    first, second = await _hold(pool, 2)
    clock.now += 125.0
    await first.release()
    clock.now += 5.0
    _apply_two_cold_snapshots(mgr, _gw())
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 1
    await second.release()


def test_unstamped_slot_is_not_quiet() -> None:
    pool = CapacityPool(event_bus=None)
    assert pool.slot_quiet_elapsed(GW, M.routing_key) is False


@pytest.mark.asyncio
async def test_idle_cold_slot_keeps_catalog_capacity(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    token = (await _hold(pool, 1))[0]
    await token.release()
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw())
    assert pool.get_slot_info(GW, M.routing_key) == (4, 0, 4)


@pytest.mark.asyncio
async def test_loaded_snapshot_breaks_absence_streak(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    gw = _apply_cold_snapshot(mgr, _gw())
    gw = _apply_cold_snapshot(mgr, gw, loaded=frozenset({M}))
    _apply_cold_snapshot(mgr, gw)
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 2
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_reset_clears_streak_so_one_snapshot_keeps_holder(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    _apply_two_cold_snapshots(mgr, _gw())
    await asyncio.sleep(0)
    await pool.acquire_token(
        request_id="req-after-reset",
        model_id=M.routing_key,
        allowed_gateway_ids=frozenset({GW}),
    )
    clock.now += 130.0
    _apply_cold_snapshot(mgr, _gw())
    _, in_flight, _ = pool.get_slot_info(GW, M.routing_key)
    assert in_flight == 1


@pytest.mark.asyncio
async def test_reset_refreshes_quiet_clock(
    pool: CapacityPool, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _install_clock(monkeypatch)
    mgr = _manager(pool)
    await _hold(pool, 2)
    clock.now += 130.0
    assert pool.slot_quiet_elapsed(GW, M.routing_key) is True
    _apply_two_cold_snapshots(mgr, _gw())
    await asyncio.sleep(0)
    assert pool.slot_quiet_elapsed(GW, M.routing_key) is False


def test_snapshot_with_model_resources_and_no_pool_returns() -> None:
    """Two absent snapshots with model_resources and no pool must not touch the pool."""
    mgr = FederatedGatewayManager(event_bus=None, capacity_pool=None)
    assert mgr._capacity_pool is None
    returned = _apply_two_cold_snapshots(mgr, _gw())
    assert returned.model_resources[M]["max_concurrent_requests"] == 4
    assert returned.available_models == frozenset({M})
    assert returned.ram_free_mb == 100_000
    assert mgr._gateways[GW] is returned
    assert mgr._cold_absent_snapshot_streak == {}
    assert mgr._capacity_pool is None
