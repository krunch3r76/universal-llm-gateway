import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    admission as admission_mod,
)


@dataclass(frozen=True)
class _FakeModelId:
    routing_key: str

    def __str__(self) -> str:
        return self.routing_key


@dataclass
class _FakeToken:
    gateway_id: str
    queued: bool = False


class _FakeCapacityPool:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str, int]] = []
        self.acquire_calls: list[tuple[str, str, frozenset[str]]] = []

    def set_capacity(self, gateway_id: str, model_id: str, max_concurrent: int) -> None:
        self.set_calls.append((gateway_id, model_id, max_concurrent))

    async def acquire_token(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> _FakeToken:
        self.acquire_calls.append((request_id, model_id, allowed_gateway_ids))
        gateway_id = next(iter(allowed_gateway_ids))
        return _FakeToken(gateway_id=gateway_id)


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


class _FakeStabilityTracker:
    def update_binding(self, model_id: Any, gateway_id: str) -> None:
        return None

    def clear_binding(self, model_id: Any) -> None:
        return None

    def get_current_best(self, model_id: Any) -> str | None:
        return None


@pytest.mark.asyncio
async def test_cold_load_uses_loading_placeholder_and_emits_catalog_capacity() -> None:
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    gateway = SimpleNamespace(
        name="edge-jupiter-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    context = SimpleNamespace(
        request_id="req-1",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=None,
    )
    pool = _FakeCapacityPool()
    event_bus = _FakeEventBus()

    selected = await admission_mod.acquire_admission_token(
        context=context,
        selected_gateway=gateway,
        gateways_for_routing=[gateway],
        routing_config={"capacity_pool": {"loading_phase_cap": 1}},
        event_bus=event_bus,
        capacity_pool=pool,
        stability_tracker=_FakeStabilityTracker(),
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
    )

    await asyncio.sleep(0)

    assert selected is gateway
    assert pool.set_calls == [("edge-jupiter-gateway", "qwen3-5-9b-q8-0-262144", 1)]
    assert pool.acquire_calls == [
        (
            "req-1",
            "qwen3-5-9b-q8-0-262144",
            frozenset({"edge-jupiter-gateway"}),
        )
    ]
    assert event_bus.events[0].signal == "routing.capacity.preseeded"
    assert event_bus.events[0].payload["placeholder_capacity"] == 1
    assert event_bus.events[0].payload["catalog_capacity"] == 32


# ── Slice 2: placeholder concurrency + allow-set ──


class _RecordingPool(_FakeCapacityPool):
    def __init__(self) -> None:
        super().__init__()
        from systems.routing.capacity.pool import CapacityPool

        self._inner = CapacityPool(event_bus=None)

    def set_capacity(self, gateway_id: str, model_id: str, max_concurrent: int) -> None:
        self.set_calls.append((gateway_id, model_id, max_concurrent))
        self._inner.set_capacity(gateway_id, model_id, max_concurrent)

    async def acquire_token(self, request_id, model_id, allowed_gateway_ids):  # type: ignore[no-untyped-def]
        self.acquire_calls.append((request_id, model_id, allowed_gateway_ids))
        return await self._inner.acquire_token(
            request_id, model_id, allowed_gateway_ids
        )

    def get_slot_info(self, gateway_id: str, model_id: str):  # type: ignore[no-untyped-def]
        return self._inner.get_slot_info(gateway_id, model_id)


def _trace_with_peer(*, b_tier: str) -> Any:
    from systems.routing.selection.decision.types import (
        DecisionTrace,
        FeasibilityTier,
        GatewayCandidate,
    )
    from systems.routing.selection.types import Gateway
    from systems.federation.common.types import FederatedGateway

    def _gw(name: str) -> Gateway:
        return Gateway(
            ref=FederatedGateway(
                gateway_id=name,
                remote_stargate_url=f"http://{name}",
                remote_stargate_id=f"{name}-remote",
            ),
            name=name,
            node_id=name,
            ram_free_mb=100_000,
            vram_free_mb=20_000,
            ram_total_mb=100_000,
            vram_total_mb=80_000,
            loaded_models=frozenset(),
        )

    tier_b = (
        FeasibilityTier.T1_FEASIBLE_NOW
        if b_tier == "T1"
        else FeasibilityTier.T2_FEASIBLE_EVICT
    )
    gw_a = _gw("edge-a-gateway")
    gw_b = _gw("edge-b-gateway")
    return DecisionTrace(
        model_id="qwen3-5-9b-q8-0-262144",
        original_model_id=None,
        request_id="req-allow",
        candidates=(
            GatewayCandidate(gateway=gw_a, tier=FeasibilityTier.T1_FEASIBLE_NOW),
            GatewayCandidate(gateway=gw_b, tier=tier_b),
        ),
        selection_tier=FeasibilityTier.T1_FEASIBLE_NOW,
        selection_reason="test",
    )


@pytest.mark.asyncio
async def test_2_3_placeholder_limits_immediate_admits() -> None:
    from systems.routing.capacity.pool import CapacityPool

    pool = CapacityPool(event_bus=None)
    pool.set_capacity("edge-a-gateway", "qwen3-5-9b-q8-0-262144", 1)
    tasks = [
        asyncio.create_task(
            pool.acquire_token(
                request_id=f"req-{i}",
                model_id="qwen3-5-9b-q8-0-262144",
                allowed_gateway_ids=frozenset({"edge-a-gateway"}),
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
        if t.done() and not t.cancelled():
            await t.result().release()


@pytest.mark.asyncio
async def test_2_6_non_sticky_cold_allow_set_includes_t1_peer() -> None:
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    gateway = SimpleNamespace(
        name="edge-a-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    peer = SimpleNamespace(
        name="edge-b-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    context = SimpleNamespace(
        request_id="req-allow",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=None,
    )
    pool = _RecordingPool()
    await admission_mod.acquire_admission_token(
        context=context,
        selected_gateway=gateway,
        gateways_for_routing=[gateway, peer],
        routing_config={"capacity_pool": {"loading_phase_cap": 1}},
        event_bus=_FakeEventBus(),
        capacity_pool=pool,
        stability_tracker=_FakeStabilityTracker(),
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
        selection_trace=_trace_with_peer(b_tier="T1"),
    )
    assert pool.acquire_calls[-1][2] == frozenset(
        {"edge-a-gateway", "edge-b-gateway"}
    )


@pytest.mark.asyncio
async def test_2_7_non_sticky_cold_t2_peer_excluded() -> None:
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    gateway = SimpleNamespace(
        name="edge-a-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    peer = SimpleNamespace(
        name="edge-b-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    context = SimpleNamespace(
        request_id="req-allow",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=None,
    )
    pool = _RecordingPool()
    await admission_mod.acquire_admission_token(
        context=context,
        selected_gateway=gateway,
        gateways_for_routing=[gateway, peer],
        routing_config={"capacity_pool": {"loading_phase_cap": 1}},
        event_bus=_FakeEventBus(),
        capacity_pool=pool,
        stability_tracker=_FakeStabilityTracker(),
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
        selection_trace=_trace_with_peer(b_tier="T2"),
    )
    assert pool.acquire_calls[-1][2] == frozenset({"edge-a-gateway"})


@pytest.mark.asyncio
async def test_2_8_sticky_cold_allow_set_is_selected_only() -> None:
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    gateway = SimpleNamespace(
        name="edge-a-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    context = SimpleNamespace(
        request_id="req-sticky",
        selected_model=model_id,
        model_sticky=True,
        capacity_token=None,
    )
    pool = _RecordingPool()
    await admission_mod.acquire_admission_token(
        context=context,
        selected_gateway=gateway,
        gateways_for_routing=[gateway],
        routing_config={"capacity_pool": {"loading_phase_cap": 1}},
        event_bus=_FakeEventBus(),
        capacity_pool=pool,
        stability_tracker=_FakeStabilityTracker(),
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
        selection_trace=_trace_with_peer(b_tier="T1"),
    )
    assert pool.acquire_calls[-1][2] == frozenset({"edge-a-gateway"})


@pytest.mark.asyncio
async def test_2_4_first_cold_request_triggers_remote_load() -> None:
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        load_and_finalize as load_mod,
    )
    from systems.federation.common.types import FederatedGateway
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision.types import (
        DecisionTrace,
        FeasibilityTier,
        GatewayCandidate,
    )
    from systems.routing.selection.types import Gateway

    class _LoadOrch:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def ensure_model_loaded_on_remote(self, *args: Any, **kwargs: Any) -> bool:
            self.calls.append(kwargs)
            return True

    load_orchestrator = _LoadOrch()
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    fed = FederatedGateway(
        gateway_id="edge-a-gateway",
        remote_stargate_url="http://a",
        remote_stargate_id="a-remote",
    )
    gateway = Gateway(
        ref=fed,
        name="edge-a-gateway",
        node_id="a",
        ram_free_mb=1,
        vram_free_mb=1,
        ram_total_mb=1,
        vram_total_mb=1,
        loaded_models=frozenset(),
    )
    pool = CapacityPool(event_bus=None)
    pool.set_capacity("edge-a-gateway", model_id.routing_key, 1)
    token = await pool.acquire_token(
        "req-leader",
        model_id.routing_key,
        frozenset({"edge-a-gateway"}),
    )
    context = SimpleNamespace(
        request_id="req-leader",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=token,
    )
    trace = DecisionTrace(
        model_id=model_id.routing_key,
        original_model_id=None,
        request_id="req-leader",
        candidates=(GatewayCandidate(gateway=gateway, tier=FeasibilityTier.T1_FEASIBLE_NOW),),
        selection_tier=FeasibilityTier.T1_FEASIBLE_NOW,
        selection_reason="test",
    )
    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=gateway,
        trace=trace,
        event_bus=_FakeEventBus(),
        federated_manager=SimpleNamespace(
            mark_loading_optimistic=lambda *a: False,
            clear_model_loading_optimistic=lambda *a: None,
        ),
        federated_load_orchestrator=load_orchestrator,
        federation_forwarder=None,
        routing_config={},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(),
        stability_tracker=_FakeStabilityTracker(),
        routing_start_time=0.0,
        eviction_cooldown_s=30.0,
        capacity_pool=pool,
    )
    assert load_orchestrator.calls[-1]["request_id"] == "req-leader"
    await token.release()


@pytest.mark.asyncio
async def test_2_5_second_cold_request_queues_when_leader_holds_slot() -> None:
    from src.scheduling.events import CAPACITY_POOL_QUEUED
    from systems.routing.capacity.pool import CapacityPool

    class _RecordingBus:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def publish_nowait(self, event: Any) -> None:
            self.events.append(event)

        def subscribe_async(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    bus = _RecordingBus()
    pool = CapacityPool(event_bus=bus)
    pool.set_capacity("edge-a-gateway", "qwen3-5-9b-q8-0-262144", 1)
    leader = await pool.acquire_token(
        "req-leader",
        "qwen3-5-9b-q8-0-262144",
        frozenset({"edge-a-gateway"}),
    )

    async def follower() -> Any:
        return await pool.acquire_token(
            "req-follower",
            "qwen3-5-9b-q8-0-262144",
            frozenset({"edge-a-gateway"}),
        )

    task = asyncio.create_task(follower())
    await asyncio.sleep(0.02)
    assert not task.done()
    queued = [event for event in bus.events if event.signal == CAPACITY_POOL_QUEUED]
    assert len(queued) == 1
    assert queued[0].payload["request_id"] == "req-follower"
    await leader.release()
    follower_token = await asyncio.wait_for(task, timeout=1.0)
    assert follower_token.queued is True
    await follower_token.release()


@pytest.mark.asyncio
async def test_2_9_waiter_reassigned_when_peer_capacity_opens() -> None:
    from systems.routing.capacity.pool import CapacityPool

    pool = CapacityPool(event_bus=None)
    pool.set_capacity("edge-a-gateway", "qwen3-5-9b-q8-0-262144", 1)
    pool.set_capacity("edge-b-gateway", "qwen3-5-9b-q8-0-262144", 0)
    leader = await pool.acquire_token(
        "req-leader",
        "qwen3-5-9b-q8-0-262144",
        frozenset({"edge-a-gateway", "edge-b-gateway"}),
    )
    waiter = asyncio.create_task(
        pool.acquire_token(
            "req-waiter",
            "qwen3-5-9b-q8-0-262144",
            frozenset({"edge-a-gateway", "edge-b-gateway"}),
        )
    )
    await asyncio.sleep(0.02)
    pool.set_capacity("edge-b-gateway", "qwen3-5-9b-q8-0-262144", 4)
    await leader.release()
    token = await asyncio.wait_for(waiter, timeout=2.0)
    assert token.gateway_id == "edge-b-gateway"
    await token.release()


async def _held_cold_peer():
    """A holds the placeholder slot; B is free with capacity 1. Trace has B at T1."""
    from systems.routing.capacity.pool import CapacityPool

    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    trace = _trace_with_peer(b_tier="T1")
    gw_a = trace.candidates[0].gateway
    gw_b = trace.candidates[1].gateway
    pool = CapacityPool(event_bus=None)
    pool.set_capacity(gw_a.name, model_id.routing_key, 1)
    pool.set_capacity(gw_b.name, model_id.routing_key, 1)
    leader = await pool.acquire_token(
        "req-leader",
        model_id.routing_key,
        frozenset({gw_a.name}),
    )
    context = SimpleNamespace(
        request_id="req-route",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=None,
        excluded_gateway_ids=set(),
    )
    return model_id, trace, gw_a, gw_b, pool, leader, context


@pytest.mark.asyncio
async def test_2_6_route_entry_admits_t1_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        entrypoint,
    )

    _model_id, trace, gw_a, gw_b, pool, leader, context = await _held_cold_peer()
    recorded: dict[str, Any] = {}

    async def _select(**_kwargs: Any) -> tuple:
        return (
            gw_a,
            trace,
            [gw_a, gw_b],
            [],
            SimpleNamespace(),
            SimpleNamespace(),
            None,
            30.0,
            None,
            None,
            0,
        )

    async def _passthrough(
        *, selected_gateway: Any, trace: Any, **_kwargs: Any
    ) -> tuple:
        return selected_gateway, trace

    async def _finalize(**kwargs: Any) -> tuple[str, None]:
        recorded["selected"] = kwargs["selected_gateway"]
        return kwargs["selected_gateway"].name, None

    monkeypatch.setattr(entrypoint, "run_initial_selection", _select)
    monkeypatch.setattr(entrypoint, "wait_for_retryable_capacity", _passthrough)
    monkeypatch.setattr(entrypoint, "finalize_selection_and_load", _finalize)
    try:
        await asyncio.wait_for(
            entrypoint._route_to_federated_gateway(
                context,
                SimpleNamespace(),
                None,
                None,
                None,
                0.0,
                routing_config={"capacity_pool": {"loading_phase_cap": 1}},
                stability_tracker=_FakeStabilityTracker(),
                capacity_pool=pool,
            ),
            1.0,
        )
        assert recorded["selected"].name == "edge-b-gateway"
    finally:
        await leader.release()
        token = context.capacity_token
        if token is not None:
            await token.release()


@pytest.mark.asyncio
async def test_2_6_requeue_entry_admits_t1_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.federation.common.types import FederatedGateway
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        eviction_requeue,
        load_and_finalize,
    )

    _model_id, trace, gw_a, _gw_b, pool, leader, context = await _held_cold_peer()
    recorded: dict[str, Any] = {}

    def _fed(name: str) -> FederatedGateway:
        return FederatedGateway(
            gateway_id=name,
            remote_stargate_url=f"http://{name}",
            remote_stargate_id=f"{name}-remote",
            loaded_models=frozenset(),
        )

    async def _wait(**_kwargs: Any) -> tuple:
        return gw_a, trace, 0

    async def _finalize(**kwargs: Any) -> tuple[str, None]:
        recorded["selected"] = kwargs["selected_gateway"]
        return kwargs["selected_gateway"].name, None

    monkeypatch.setattr(eviction_requeue, "_wait_and_retry_selection", _wait)
    monkeypatch.setattr(load_and_finalize, "finalize_selection_and_load", _finalize)
    manager = SimpleNamespace(
        get_all_gateways=lambda: [
            _fed("edge-a-gateway"),
            _fed("edge-b-gateway"),
        ]
    )
    try:
        await asyncio.wait_for(
            eviction_requeue.requeue_after_transient_eviction(
                context=context,
                federated_manager=manager,
                federated_load_orchestrator=None,
                federation_forwarder=None,
                routing_config={"capacity_pool": {"loading_phase_cap": 1}},
                decision_engine=SimpleNamespace(),
                placement=SimpleNamespace(),
                event_bus=None,
                stability_tracker=_FakeStabilityTracker(),
                routing_start_time=0.0,
                eviction_cooldown_s=30.0,
                capacity_pool=pool,
                routing_key_tracker=None,
                continuation_mode="transient_capacity",
                timeout_reason="test",
            ),
            1.0,
        )
        assert recorded["selected"].name == "edge-b-gateway"
    finally:
        await leader.release()
        token = context.capacity_token
        if token is not None:
            await token.release()
