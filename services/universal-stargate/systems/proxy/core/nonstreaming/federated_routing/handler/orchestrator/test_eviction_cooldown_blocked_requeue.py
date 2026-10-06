"""BLOCKED cooldown requeue, hold-bounded select, and post-wait eviction sequencing."""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402
from universal_protocol import ErrorCode  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    admission as admission_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    eviction_execution as eviction_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    eviction_requeue as requeue_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    load_and_finalize as load_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (  # noqa: E402
    continuation_still_transient,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_logic import (  # noqa: E402
    _wait_and_retry_selection,
)
from systems.routing.selection.decision.eviction_cooldown_policy import (  # noqa: E402
    CooldownOverrideKey,
    clear_cooldown_override_tracker,
    oscillation_hold_remaining_s,
    record_cooldown_override,
)
from systems.routing.selection.decision.types import (  # noqa: E402
    DecisionTrace,
    EvictionPlanSummary,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.routing.selection.types import Gateway  # noqa: E402

GEMMA = ModelId.parse("gemma-3-27b-q4-0-8192")
TARGET = ModelId.parse("qwen3-32b-awq-16384")


@dataclass
class _FakeEventBus:
    events: list[Any] = field(default_factory=list)

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _FakeForwarder:
    unload_calls: list[Any] = field(default_factory=list)

    async def forward_model_unload_request(self, **kwargs: Any) -> dict[str, str]:
        self.unload_calls.append(kwargs)
        return {"status": "ok"}


@dataclass
class _FakeLoadOrchestrator:
    load_calls: list[Any] = field(default_factory=list)

    async def ensure_model_loaded_on_remote(self, *args: Any, **kwargs: Any) -> bool:
        self.load_calls.append((args, kwargs))
        return True


class _FakeToken:
    def __init__(self, gateway_id: str) -> None:
        self.gateway_id = gateway_id
        self.queued = False
        self.released = False

    async def release(self) -> None:
        self.released = True


class _FakeCapacityPool:
    def pause_admission(
        self, routing_key: str, *, duration_s: float, reason: str
    ) -> None:
        return None

    async def acquire_token(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> _FakeToken:
        return _FakeToken(gateway_id=next(iter(allowed_gateway_ids)))


class _FakeFederatedManager:
    def __init__(self, gateways: list[Any] | None = None) -> None:
        self._gateways = gateways or []
        self.clear_calls: list[tuple[Any, Any]] = []
        self.wait_timeouts: list[float] = []

    def get_all_gateways(self) -> list[Any]:
        return self._gateways

    def get_state_version(self) -> int:
        return 1

    async def wait_for_state_change(self, _version: int, timeout: float) -> None:
        self.wait_timeouts.append(timeout)
        return None

    def mark_loading_optimistic(self, _gateway_id: str, _model_id: Any) -> bool:
        return True

    def clear_model_loading_optimistic(self, gateway_id: str, model_id: Any) -> None:
        self.clear_calls.append((gateway_id, model_id))


def _fed_gateway() -> FederatedGateway:
    return FederatedGateway(
        gateway_id="edge-jupiter-gateway",
        remote_stargate_url="http://jupiter",
        remote_stargate_id="jupiter-remote",
    )


def _gateway(*, loaded: frozenset[ModelId] | None = None) -> Gateway:
    return Gateway(
        ref=_fed_gateway(),
        name="edge-jupiter-gateway",
        node_id="jupiter",
        ram_free_mb=100_000,
        vram_free_mb=1_000,
        ram_total_mb=100_000,
        vram_total_mb=80_000,
        loaded_models=loaded if loaded is not None else frozenset({GEMMA}),
    )


def _plan() -> EvictionPlanSummary:
    return EvictionPlanSummary(
        models_to_evict=frozenset({GEMMA}),
        freed_vram_mb=12_000,
        freed_ram_mb=0,
        estimated_cost=-50.0,
        cooldown_override_pending=True,
        cooldown_override_victim_id=str(GEMMA),
        cooldown_override_remaining_s=45.0,
        trigger_model_id=str(TARGET),
    )


def _trace() -> DecisionTrace:
    return DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req-cooldown-requeue",
        candidates=(
            GatewayCandidate(
                gateway=_gateway(),
                tier=FeasibilityTier.T2_FEASIBLE_EVICT,
                eviction_plan=_plan(),
            ),
        ),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
        selection_reason="test",
    )


@pytest.fixture(autouse=True)
def _reset_override_tracker() -> None:
    clear_cooldown_override_tracker()


def test_oscillation_hold_remaining_shrinks_on_read() -> None:
    key = CooldownOverrideKey(
        gateway_id="edge-jupiter-gateway", victim_model_id=str(GEMMA)
    )
    now = 1_000.0
    record_cooldown_override(key, now=now)
    assert oscillation_hold_remaining_s(key, now=now + 10.0) == pytest.approx(110.0)
    assert oscillation_hold_remaining_s(key, now=now + 120.0) == 0.0
    assert oscillation_hold_remaining_s(key, now=now + 121.0) == 0.0


def test_cooldown_blocked_continuation_while_hold_active() -> None:
    key = CooldownOverrideKey(
        gateway_id="edge-jupiter-gateway", victim_model_id=str(GEMMA)
    )
    record_cooldown_override(key)
    empty = DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req",
        candidates=(),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
    )
    assert (
        continuation_still_transient(
            empty, mode="cooldown_blocked", cooldown_hold_key=key
        )
        is True
    )


@pytest.mark.asyncio
async def test_wait_loop_at_most_one_select_per_wake_while_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = CooldownOverrideKey(
        gateway_id="edge-jupiter-gateway", victim_model_id=str(GEMMA)
    )
    clock = {"t": 0.0}
    record_cooldown_override(key, now=0.0)

    def _mono() -> float:
        return clock["t"]

    monkeypatch.setattr(time, "monotonic", _mono)
    monkeypatch.setattr(
        "systems.routing.selection.decision.eviction_cooldown_policy.time.monotonic",
        _mono,
    )

    select_calls = {"n": 0}
    selected = _gateway(loaded=frozenset())

    class _Engine:
        def select(self, **kwargs: Any) -> tuple[Any, Any]:
            select_calls["n"] += 1
            return selected, _trace()

    manager = _FakeFederatedManager(
        [
            SimpleNamespace(
                gateway_id="edge-jupiter-gateway",
                dispatchable=True,
                available_models=frozenset({TARGET, GEMMA}),
            )
        ]
    )

    async def _advance_wait(_version: int, timeout: float) -> None:
        manager.wait_timeouts.append(timeout)
        clock["t"] += timeout

    manager.wait_for_state_change = _advance_wait  # type: ignore[method-assign]

    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [selected],
    )

    result_gw, _trace_out, _waited = await _wait_and_retry_selection(
        federated_manager=manager,
        decision_engine=_Engine(),
        placement=SimpleNamespace(model_id=TARGET),
        context=SimpleNamespace(
            request_id="req-hold",
            selected_model=TARGET,
            model_sticky=False,
            excluded_gateway_ids=set(),
        ),
        event_bus=None,
        timeout_s=200.0,
        stability_tracker=SimpleNamespace(),
        continuation_mode="cooldown_blocked",
        cooldown_hold_key=key,
        per_wake_cap_s=30.0,
    )

    assert result_gw is selected
    assert select_calls["n"] == 1
    assert manager.wait_timeouts
    assert all(t <= 30.0 for t in manager.wait_timeouts)


@pytest.mark.asyncio
async def test_blocked_transient_requeues_after_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = _FakeEventBus()
    load_orchestrator = _FakeLoadOrchestrator()
    token = _FakeToken("edge-jupiter-gateway")
    manager = _FakeFederatedManager(
        [
            SimpleNamespace(
                gateway_id="edge-jupiter-gateway",
                dispatchable=True,
                available_models=frozenset({TARGET, GEMMA}),
            )
        ]
    )
    context = SimpleNamespace(
        request_id="req-blocked-requeue",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=token,
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() + 60.0,
    )
    reselected = _gateway(loaded=frozenset())
    wait_mock = AsyncMock(return_value=(reselected, _trace(), 12))
    exec_calls = {"n": 0}

    async def _exec_toggle(**kwargs: Any) -> Any:
        exec_calls["n"] += 1
        if exec_calls["n"] == 1:
            return eviction_mod.MasterEvictionResult(
                outcome=eviction_mod.MasterEvictionOutcome.BLOCKED,
                reason="cooldown_oscillation_breaker",
                retry_after_s=45.0,
                verdict_class="insufficient_transient",
                gateway_id="edge-jupiter-gateway",
                victim_model_id=str(GEMMA),
                requester="req-blocked-requeue",
            )
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EVICTED,
            gateway_id="edge-jupiter-gateway",
            requester="req-blocked-requeue",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _exec_toggle,
    )
    monkeypatch.setattr(requeue_mod, "_wait_and_retry_selection", wait_mock)
    admission_mock = AsyncMock(return_value=reselected)
    monkeypatch.setattr(admission_mod, "acquire_admission_token", admission_mock)
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [reselected],
    )

    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=_gateway(),
        trace=_trace(),
        event_bus=event_bus,
        federated_manager=manager,
        federated_load_orchestrator=load_orchestrator,
        federation_forwarder=_FakeForwarder(),
        routing_config={"drain_duration_s": 30.0},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(model_id=TARGET),
        stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
        routing_start_time=time.time(),
        eviction_cooldown_s=120.0,
        capacity_pool=_FakeCapacityPool(),
    )

    assert token.released is True
    assert manager.clear_calls
    wait_mock.assert_awaited_once()
    assert wait_mock.await_args.kwargs["continuation_mode"] == "cooldown_blocked"
    assert wait_mock.await_args.kwargs["per_wake_cap_s"] == 45.0
    assert exec_calls["n"] == 2
    assert len(load_orchestrator.load_calls) == 1


@pytest.mark.asyncio
async def test_blocked_deadline_past_raises_capacity_without_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        request_id="req-deadline-blocked",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=_FakeToken("edge-jupiter-gateway"),
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() - 1.0,
    )
    load_orchestrator = _FakeLoadOrchestrator()

    async def _blocked(**kwargs: Any) -> Any:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.BLOCKED,
            reason="cooldown_oscillation_breaker",
            retry_after_s=45.0,
            verdict_class="insufficient_transient",
            gateway_id="edge-jupiter-gateway",
            victim_model_id=str(GEMMA),
            requester="req-deadline-blocked",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _blocked,
    )
    monkeypatch.setattr(
        requeue_mod,
        "_wait_and_retry_selection",
        AsyncMock(return_value=(None, _trace(), 0)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await load_mod.finalize_selection_and_load(
            context=context,
            selected_gateway=_gateway(),
            trace=_trace(),
            event_bus=_FakeEventBus(),
            federated_manager=_FakeFederatedManager([]),
            federated_load_orchestrator=load_orchestrator,
            federation_forwarder=_FakeForwarder(),
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=TARGET),
            stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
            routing_start_time=time.time(),
            eviction_cooldown_s=120.0,
            capacity_pool=_FakeCapacityPool(),
        )

    detail = exc_info.value.detail
    assert detail["code"] == ErrorCode.STICKY_CAPACITY
    assert detail["data"]["reason"] == "eviction_blocked_queue_timeout"
    assert load_orchestrator.load_calls == []


@pytest.mark.asyncio
async def test_post_load_vram_failure_reenters_finalize_not_direct_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_orchestrator = _FakeLoadOrchestrator()
    load_calls_before_requeue = {"n": 0}

    async def _load_then_fail(*args: Any, **kwargs: Any) -> bool:
        load_orchestrator.load_calls.append((args, kwargs))
        load_calls_before_requeue["n"] += 1
        if load_calls_before_requeue["n"] == 1:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": ErrorCode.INSUFFICIENT_VRAM,
                    "retryable": True,
                    "message": "need 30262 have 11780",
                },
            )
        return True

    load_orchestrator.ensure_model_loaded_on_remote = _load_then_fail  # type: ignore[method-assign]

    reselected = _gateway(loaded=frozenset({TARGET}))
    wait_mock = AsyncMock(return_value=(reselected, _trace(), 5))
    monkeypatch.setattr(requeue_mod, "_wait_and_retry_selection", wait_mock)
    monkeypatch.setattr(
        admission_mod, "acquire_admission_token", AsyncMock(return_value=reselected)
    )
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [reselected],
    )

    exec_calls = {"n": 0}

    async def _always_evict(**kwargs: Any) -> Any:
        exec_calls["n"] += 1
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EVICTED,
            gateway_id="edge-jupiter-gateway",
            requester="req-vram",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _always_evict,
    )

    context = SimpleNamespace(
        request_id="req-vram",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=_FakeToken("edge-jupiter-gateway"),
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() + 60.0,
    )

    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=_gateway(),
        trace=_trace(),
        event_bus=_FakeEventBus(),
        federated_manager=_FakeFederatedManager(
            [
                SimpleNamespace(
                    gateway_id="edge-jupiter-gateway",
                    dispatchable=True,
                    available_models=frozenset({TARGET, GEMMA}),
                )
            ]
        ),
        federated_load_orchestrator=load_orchestrator,
        federation_forwarder=_FakeForwarder(),
        routing_config={},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(model_id=TARGET),
        stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
        routing_start_time=time.time(),
        eviction_cooldown_s=120.0,
        capacity_pool=_FakeCapacityPool(),
    )

    wait_mock.assert_awaited_once()
    # First load fails with VRAM; requeue re-enters finalize which evicts then loads.
    assert exec_calls["n"] >= 2
    assert len(load_orchestrator.load_calls) == 2


@pytest.mark.asyncio
async def test_blocked_wait_cancelled_emits_and_clears_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _FakeFederatedManager([])
    token = _FakeToken("edge-jupiter-gateway")
    context = SimpleNamespace(
        request_id="req-cancel",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=token,
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() + 60.0,
    )

    async def _blocked(**kwargs: Any) -> Any:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.BLOCKED,
            reason="cooldown_oscillation_breaker",
            retry_after_s=10.0,
            verdict_class="insufficient_transient",
            gateway_id="edge-jupiter-gateway",
            victim_model_id=str(GEMMA),
            requester="req-cancel",
        )

    async def _cancel_wait(**kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _blocked,
    )
    monkeypatch.setattr(requeue_mod, "_wait_and_retry_selection", _cancel_wait)

    with pytest.raises(asyncio.CancelledError):
        await load_mod.finalize_selection_and_load(
            context=context,
            selected_gateway=_gateway(),
            trace=_trace(),
            event_bus=_FakeEventBus(),
            federated_manager=manager,
            federated_load_orchestrator=_FakeLoadOrchestrator(),
            federation_forwarder=_FakeForwarder(),
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=TARGET),
            stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
            routing_start_time=time.time(),
            eviction_cooldown_s=120.0,
            capacity_pool=_FakeCapacityPool(),
        )

    assert token.released is True
    assert manager.clear_calls
