"""Slice 1 acceptance tests 1.0–1.9: cancel paths release admission tokens."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    admission as admission_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    load_and_finalize as load_mod,
)
from systems.routing.capacity.pool import CapacityPool  # noqa: E402
from systems.routing.selection.decision.types import (  # noqa: E402
    DecisionTrace,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402

M = ModelId.parse("qwen3-5-9b-q8-0-131072")
GW = "edge-localhost-gateway"


@dataclass
class _FakeEventBus:
    events: list[Any] = field(default_factory=list)

    def subscribe_async(self, signal: str, callback: Any) -> None:
        return None

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _FakeLoadOrchestrator:
    cancel_during_load: bool = True
    entered: asyncio.Event | None = None
    hold_until: asyncio.Event | None = None

    async def ensure_model_loaded_on_remote(self, *args: Any, **kwargs: Any) -> bool:
        if self.entered is not None:
            self.entered.set()
        if self.hold_until is not None:
            await self.hold_until.wait()
        if self.cancel_during_load:
            raise asyncio.CancelledError()
        return True


class _FakeFederatedManager:
    def __init__(self) -> None:
        self.clear_calls: list[tuple[str, Any]] = []

    def mark_loading_optimistic(self, gateway_id: str, model_id: Any) -> bool:
        return True

    def clear_model_loading_optimistic(self, gateway_id: str, model_id: Any) -> None:
        self.clear_calls.append((gateway_id, model_id))


def _fed_gateway() -> FederatedGateway:
    return FederatedGateway(
        gateway_id=GW,
        remote_stargate_url="http://localhost",
        remote_stargate_id="localhost-remote",
    )


def _gateway(*, loaded: frozenset[ModelId] | None = None) -> Gateway:
    return Gateway(
        ref=_fed_gateway(),
        name=GW,
        node_id="localhost",
        ram_free_mb=100_000,
        vram_free_mb=20_000,
        ram_total_mb=100_000,
        vram_total_mb=80_000,
        loaded_models=loaded or frozenset(),
        model_details={
            M: {"max_concurrent_requests": 4},
        },
    )


def _trace_t1() -> DecisionTrace:
    gw = _gateway()
    return DecisionTrace(
        model_id=str(M),
        original_model_id=None,
        request_id="req-cancel",
        candidates=(
            GatewayCandidate(
                gateway=gw,
                tier=FeasibilityTier.T1_FEASIBLE_NOW,
            ),
        ),
        selection_tier=FeasibilityTier.T1_FEASIBLE_NOW,
        selection_reason="test",
    )


async def _finalize_cancel_during_load(
    pool: CapacityPool,
    *,
    federated_manager: _FakeFederatedManager | None = None,
    load_orchestrator: _FakeLoadOrchestrator | None = None,
) -> None:
    gateway = _gateway()
    token = await pool.acquire_token(
        request_id="req-cancel",
        model_id=M.routing_key,
        allowed_gateway_ids=frozenset({GW}),
    )
    context = SimpleNamespace(
        request_id="req-cancel",
        selected_model=M,
        model_sticky=True,
        capacity_token=token,
        selected_gateway=None,
    )
    if load_orchestrator is None:
        load_orchestrator = _FakeLoadOrchestrator(cancel_during_load=True)
    fm = federated_manager or _FakeFederatedManager()
    try:
        await load_mod.finalize_selection_and_load(
            context=context,
            selected_gateway=gateway,
            trace=_trace_t1(),
            event_bus=_FakeEventBus(),
            federated_manager=fm,
            federated_load_orchestrator=load_orchestrator,
            federation_forwarder=None,
            routing_config={"capacity_pool": {"loading_phase_cap": 1}},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(),
            stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
            routing_start_time=time.time(),
            eviction_cooldown_s=30.0,
            capacity_pool=pool,
        )
    except asyncio.CancelledError:
        pass
    else:
        pytest.fail("expected CancelledError from load orchestrator")


@pytest.fixture
def pool() -> CapacityPool:
    p = CapacityPool(event_bus=None)
    p.set_capacity(GW, M.routing_key, 1)
    return p


@pytest.mark.skip(
    reason="pre-fix gate only; closeout records 1 passed before Slice 1 prod edit"
)
@pytest.mark.asyncio
async def test_1_0_prefixed_cancel_leaks_in_flight_from_pool(
    pool: CapacityPool,
) -> None:
    """Pre-fix gate: cancel during load leaves in_flight elevated (C1 repro)."""
    _avail_before, in_flight_before, _cap = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_before == 0
    await _finalize_cancel_during_load(pool)
    _avail_after, in_flight_after, _cap_after = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_after == in_flight_before + 1, (
        f"expected leak in_flight={in_flight_before + 1}, got {in_flight_after}"
    )


@pytest.mark.asyncio
async def test_1_1_cancel_during_load_releases_in_flight(pool: CapacityPool) -> None:
    _avail_before, in_flight_before, _cap = pool.get_slot_info(GW, M.routing_key)
    await _finalize_cancel_during_load(pool)
    _avail_after, in_flight_after, _cap_after = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_after == in_flight_before


@pytest.mark.asyncio
async def test_1_2_cancel_clears_optimistic_loading_mark_once(
    pool: CapacityPool,
) -> None:
    fm = _FakeFederatedManager()
    await _finalize_cancel_during_load(pool, federated_manager=fm)
    assert fm.clear_calls == [(GW, M)]


@pytest.mark.asyncio
async def test_1_3_cancel_releases_slot_for_queued_waiter() -> None:
    bus = _FakeEventBus()
    pool = CapacityPool(event_bus=bus)
    pool.set_capacity(GW, M.routing_key, 1)
    entered = asyncio.Event()
    proceed = asyncio.Event()
    orchestrator = _FakeLoadOrchestrator(
        cancel_during_load=True,
        entered=entered,
        hold_until=proceed,
    )

    async def holder_then_cancel() -> None:
        await _finalize_cancel_during_load(pool, load_orchestrator=orchestrator)

    holder = asyncio.create_task(holder_then_cancel())
    await asyncio.wait_for(entered.wait(), timeout=2.0)

    waiter_task = asyncio.create_task(
        pool.acquire_token(
            request_id="req-wait",
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )
    )
    await asyncio.sleep(0.05)
    assert not waiter_task.done()
    proceed.set()
    try:
        await holder
    except asyncio.CancelledError:
        pass

    token_w = await asyncio.wait_for(waiter_task, timeout=2.0)
    await asyncio.sleep(0)
    assert token_w.gateway_id == GW
    assert any(
        getattr(event, "signal", "") == "capacity.pool.admitted" for event in bus.events
    )
    _a, in_flight, _c = pool.get_slot_info(GW, M.routing_key)
    assert in_flight >= 1
    await token_w.release()


@pytest.mark.asyncio
async def test_1_4_cancel_while_waiting_in_queue_does_not_change_in_flight(
    pool: CapacityPool,
) -> None:
    token_a = await pool.acquire_token(
        request_id="req-a",
        model_id=M.routing_key,
        allowed_gateway_ids=frozenset({GW}),
    )
    _a, in_flight_before, _c = pool.get_slot_info(GW, M.routing_key)

    async def acquire_b() -> None:
        await pool.acquire_token(
            request_id="req-b",
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )

    task_b = asyncio.create_task(acquire_b())
    await asyncio.sleep(0.01)
    task_b.cancel()
    await asyncio.sleep(0.05)
    assert task_b.cancelled()
    _a2, in_flight_mid, _c2 = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_mid == in_flight_before
    await token_a.release()


@pytest.mark.asyncio
async def test_1_5_successful_finalize_no_invariant_releasing_log(
    pool: CapacityPool, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = _gateway(loaded=frozenset({M}))
    token = await pool.acquire_token(
        request_id="req-ok",
        model_id=M.routing_key,
        allowed_gateway_ids=frozenset({GW}),
    )
    context = SimpleNamespace(
        request_id="req-ok",
        selected_model=M,
        model_sticky=True,
        capacity_token=token,
        selected_gateway=None,
    )
    caplog.set_level(logging.ERROR)
    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=gateway,
        trace=_trace_t1(),
        event_bus=_FakeEventBus(),
        federated_manager=_FakeFederatedManager(),
        federated_load_orchestrator=_FakeLoadOrchestrator(cancel_during_load=False),
        federation_forwarder=None,
        routing_config={},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(),
        stability_tracker=SimpleNamespace(get_current_best=lambda _m: GW),
        routing_start_time=time.time(),
        eviction_cooldown_s=30.0,
        capacity_pool=pool,
    )
    assert not any("Invariant: releasing" in r.message for r in caplog.records)
    await token.release()


@pytest.mark.asyncio
async def test_1_6_cancelled_during_emit_orchestrator_decision_releases_token(
    pool: CapacityPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.proxy.core.nonstreaming.executor.core import RequestExecutor
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        entrypoint as entrypoint_mod,
    )

    context = SimpleNamespace(
        request_id="req-orch",
        selected_model=M,
        capacity_token=None,
    )
    executor = RequestExecutor.__new__(RequestExecutor)

    async def _emit_cancel(**_kwargs: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(entrypoint_mod, "_emit_orchestrator_decision", _emit_cancel)

    async def _select_cancel(ctx: Any) -> None:
        token = await pool.acquire_token(
            request_id="req-orch",
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )
        ctx.capacity_token = token
        await entrypoint_mod._emit_orchestrator_decision(
            event_bus=_FakeEventBus(),
            context=ctx,
            selected_gateway=_gateway(),
            trace=_trace_t1(),
            gateways_for_routing=[_gateway()],
        )

    executor._select_gateway_and_load_model = _select_cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await executor._execute_bypass_mode(context)

    _a2, in_flight_after, _c2 = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_after == 0


@pytest.mark.asyncio
async def test_1_7_cancelled_during_overflow_event_releases_token(
    pool: CapacityPool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.proxy.core.nonstreaming.executor.core import RequestExecutor

    gateway = _gateway(loaded=frozenset({M}))
    context = SimpleNamespace(
        request_id="req-of",
        selected_model=M,
        model_sticky=False,
        capacity_token=None,
        selected_gateway=None,
    )
    executor = RequestExecutor.__new__(RequestExecutor)

    async def _overflow_boom(**_kwargs: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(admission_mod, "_emit_overflow_assigned_event", _overflow_boom)

    async def _select_overflow(ctx: Any) -> None:
        await admission_mod.acquire_admission_token(
            context=ctx,
            selected_gateway=gateway,
            gateways_for_routing=[gateway],
            routing_config={},
            event_bus=_FakeEventBus(),
            capacity_pool=pool,
            stability_tracker=SimpleNamespace(),
            allowed_gateway_ids_override=None,
            overflow_origin_gateway="other-gw",
            overflow_depth_before=0,
        )

    executor._select_gateway_and_load_model = _select_overflow  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await executor._execute_bypass_mode(context)

    _a2, in_flight_after, _c2 = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_after == 0


@pytest.mark.asyncio
async def test_1_8_fed_gateway_none_after_select_releases_token(
    pool: CapacityPool,
) -> None:
    from fastapi import HTTPException

    from systems.proxy.core.nonstreaming.executor.core import RequestExecutor

    context = SimpleNamespace(
        request_id="req-none",
        selected_model=M,
        capacity_token=None,
        federated_gateway=None,
    )
    executor = RequestExecutor.__new__(RequestExecutor)

    async def _select_clear_fed(ctx: Any) -> None:
        token = await pool.acquire_token(
            request_id="req-none",
            model_id=M.routing_key,
            allowed_gateway_ids=frozenset({GW}),
        )
        ctx.capacity_token = token
        ctx.federated_gateway = None

    executor._select_gateway_and_load_model = _select_clear_fed  # type: ignore[method-assign]

    with pytest.raises(HTTPException):
        await executor._execute_normal_mode(context)

    _a2, in_flight_after, _c2 = pool.get_slot_info(GW, M.routing_key)
    assert in_flight_after == 0


@pytest.mark.asyncio
async def test_1_9_cancel_during_token_release_nulls_context_reference(
    pool: CapacityPool,
) -> None:
    from systems.proxy.core.nonstreaming.executor.core import RequestExecutor

    class _TokenReleaseCancel:
        async def release(self) -> None:
            raise asyncio.CancelledError()

    context = SimpleNamespace(capacity_token=_TokenReleaseCancel())
    executor = RequestExecutor.__new__(RequestExecutor)
    try:
        await RequestExecutor._release_capacity_token(executor, context)
    except asyncio.CancelledError:
        pass
    assert context.capacity_token is None
