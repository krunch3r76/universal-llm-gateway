"""Integration tests for orchestrator eviction execution and load sequencing."""

from __future__ import annotations

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

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    eviction_execution as eviction_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    load_and_finalize as load_mod,
)
from systems.routing.selection.decision.eviction_cooldown_policy import (  # noqa: E402
    clear_cooldown_override_tracker,
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


def _fed_gateway() -> FederatedGateway:
    return FederatedGateway(
        gateway_id="edge-jupiter-gateway",
        remote_stargate_url="http://jupiter",
        remote_stargate_id="jupiter-remote",
    )


def _gateway() -> Gateway:
    return Gateway(
        ref=_fed_gateway(),
        name="edge-jupiter-gateway",
        node_id="jupiter",
        ram_free_mb=100_000,
        vram_free_mb=1_000,
        ram_total_mb=100_000,
        vram_total_mb=80_000,
        loaded_models=frozenset({GEMMA}),
    )


def _override_plan() -> EvictionPlanSummary:
    return EvictionPlanSummary(
        models_to_evict=frozenset({GEMMA}),
        freed_vram_mb=12_000,
        freed_ram_mb=0,
        estimated_cost=-50.0,
        cooldown_override_pending=True,
        cooldown_override_victim_id=str(GEMMA),
        cooldown_override_remaining_s=101.0,
        trigger_model_id=str(TARGET),
    )


def _trace(plan: EvictionPlanSummary) -> DecisionTrace:
    return DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req-gemma-override",
        candidates=(
            GatewayCandidate(
                gateway=_gateway(),
                tier=FeasibilityTier.T2_FEASIBLE_EVICT,
                eviction_plan=plan,
            ),
        ),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
    )


def _confirmed_eviction_outcome() -> SimpleNamespace:
    return SimpleNamespace(ok=True)


@pytest.fixture(autouse=True)
def _reset_override_tracker() -> None:
    clear_cooldown_override_tracker()


@pytest.mark.asyncio
async def test_required_override_emits_cooldown_overridden_and_evicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = _FakeEventBus()
    forwarder = _FakeForwarder()
    monkeypatch.setattr(
        "systems.routing.eviction.executor.execute_eviction_plan",
        AsyncMock(return_value=_confirmed_eviction_outcome()),
    )

    result = await eviction_mod.execute_master_eviction(
        federation_forwarder=forwarder,
        federated_manager=None,
        selected_gateway=_gateway(),
        trace=_trace(_override_plan()),
        request_id="req-gemma-override",
        event_bus=event_bus,
    )

    assert result.outcome == eviction_mod.MasterEvictionOutcome.EVICTED
    assert len(event_bus.events) == 1
    assert event_bus.events[0].signal == "scheduler.eviction.cooldown.overridden"
    payload = event_bus.events[0].payload
    assert payload["model"] == str(GEMMA)
    assert payload["node"] == "jupiter"
    assert payload["remaining_s"] == pytest.approx(101.0)
    assert payload["requester"] == "req-gemma-override"


@pytest.mark.asyncio
async def test_second_override_within_window_returns_insufficient_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.routing.eviction.executor.execute_eviction_plan",
        AsyncMock(return_value=_confirmed_eviction_outcome()),
    )
    trace = _trace(_override_plan())
    kwargs = dict(
        federation_forwarder=_FakeForwarder(),
        federated_manager=None,
        selected_gateway=_gateway(),
        trace=trace,
        request_id="req-1",
        event_bus=_FakeEventBus(),
    )

    first = await eviction_mod.execute_master_eviction(**kwargs)
    second = await eviction_mod.execute_master_eviction(
        **{**kwargs, "request_id": "req-2", "event_bus": _FakeEventBus()}
    )

    assert first.outcome == eviction_mod.MasterEvictionOutcome.EVICTED
    assert second.outcome == eviction_mod.MasterEvictionOutcome.BLOCKED
    assert second.verdict_class == "insufficient_transient"
    assert second.retry_after_s is not None
    assert second.retry_after_s > 0


@pytest.mark.asyncio
async def test_blocked_eviction_skips_load_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_orchestrator = _FakeLoadOrchestrator()
    context = SimpleNamespace(
        request_id="req-blocked",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=None,
        selected_gateway=None,
    )
    plan = _override_plan()
    trace = DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req-blocked",
        candidates=(
            GatewayCandidate(
                gateway=_gateway(),
                tier=FeasibilityTier.T2_FEASIBLE_EVICT,
                eviction_plan=plan,
            ),
        ),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
        selection_reason="test",
    )

    async def _blocked_eviction(**kwargs: Any) -> eviction_mod.MasterEvictionResult:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.BLOCKED,
            reason="cooldown_oscillation_breaker",
            retry_after_s=45.0,
            verdict_class="insufficient_transient",
            gateway_id="edge-jupiter-gateway",
            requester="req-blocked",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _blocked_eviction,
    )

    started = time.monotonic()
    with pytest.raises(HTTPException) as exc_info:
        await load_mod.finalize_selection_and_load(
            context=context,
            selected_gateway=_gateway(),
            trace=trace,
            event_bus=_FakeEventBus(),
            federated_manager=None,
            federated_load_orchestrator=load_orchestrator,
            federation_forwarder=_FakeForwarder(),
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(),
            stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
            routing_start_time=time.time(),
            eviction_cooldown_s=120.0,
        )
    elapsed = time.monotonic() - started

    assert load_orchestrator.load_calls == []
    assert elapsed <= 2.0
    detail = exc_info.value.detail
    assert detail["data"]["verdict_class"] == "insufficient_transient"
    assert detail["data"]["retry_after_s"] == 45.0


@pytest.mark.asyncio
async def test_evicted_path_dispatches_exactly_one_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_orchestrator = _FakeLoadOrchestrator()
    context = SimpleNamespace(
        request_id="req-success",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=None,
        selected_gateway=None,
    )
    trace = _trace(_override_plan())

    async def _evicted(**kwargs: Any) -> eviction_mod.MasterEvictionResult:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EVICTED,
            gateway_id="edge-jupiter-gateway",
            requester="req-success",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _evicted,
    )

    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=_gateway(),
        trace=trace,
        event_bus=_FakeEventBus(),
        federated_manager=None,
        federated_load_orchestrator=load_orchestrator,
        federation_forwarder=_FakeForwarder(),
        routing_config={},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(),
        stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
        routing_start_time=time.time(),
        eviction_cooldown_s=120.0,
    )

    assert len(load_orchestrator.load_calls) == 1
