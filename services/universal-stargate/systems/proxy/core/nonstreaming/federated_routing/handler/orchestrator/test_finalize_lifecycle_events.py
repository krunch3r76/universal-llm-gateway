"""Lifecycle observation events for T2 eviction execute and remote model load."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    eviction_execution as eviction_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    load_and_finalize as load_mod,
)
from systems.routing.selection.decision.types import (  # noqa: E402
    DecisionTrace,
    EvictionPlanSummary,
    FeasibilityTier,
    GatewayCandidate,
)
from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.routing.selection.types import Gateway  # noqa: E402

TARGET = ModelId.parse("qwen3-32b-awq-16384")
VICTIM = ModelId.parse("gemma-3-27b-q4-0-8192")


@dataclass
class _FakeEventBus:
    events: list[Any] = field(default_factory=list)

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


@dataclass
class _FakeLoadOrchestrator:
    load_calls: list[Any] = field(default_factory=list)
    _event_bus: _FakeEventBus | None = None

    async def ensure_model_loaded_on_remote(self, *args: Any, **kwargs: Any) -> bool:
        self.load_calls.append((args, kwargs))
        return True


class _FakeForwarder:
    async def forward_model_unload_request(self, **kwargs: Any) -> dict[str, str]:
        return {"status": "ok"}


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
        loaded_models=frozenset({VICTIM}),
    )


def _plan() -> EvictionPlanSummary:
    return EvictionPlanSummary(
        models_to_evict=frozenset({VICTIM}),
        freed_vram_mb=12000,
        freed_ram_mb=500,
        estimated_cost=1.0,
        cooldown_protected_count=0,
        demand_protected_count=0,
    )


def _trace() -> DecisionTrace:
    return DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req-lifecycle",
        candidates=(
            GatewayCandidate(
                gateway=_gateway(),
                tier=FeasibilityTier.T2_FEASIBLE_EVICT,
                eviction_plan=_plan(),
            ),
        ),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
    )


@pytest.mark.asyncio
async def test_eviction_execute_emits_started_and_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeEventBus()
    load_orchestrator = _FakeLoadOrchestrator(_event_bus=bus)
    context = SimpleNamespace(
        request_id="req-lifecycle",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=None,
        selected_gateway=None,
    )

    async def _evicted(**kwargs: Any) -> eviction_mod.MasterEvictionResult:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EVICTED,
            gateway_id="edge-jupiter-gateway",
            requester="req-lifecycle",
        )

    monkeypatch.setattr(eviction_mod, "execute_master_eviction", _evicted)

    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=_gateway(),
        trace=_trace(),
        event_bus=bus,
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

    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "routing.eviction.execute.started" in signals
    assert "routing.eviction.execute.finished" in signals
    started = next(
        e for e in bus.events if e.signal == "routing.eviction.execute.started"
    )
    assert VICTIM.routing_key in started.payload["victim_model_ids"]
    assert started.payload["freed_vram_mb"] == 12000
    finished = next(
        e for e in bus.events if e.signal == "routing.eviction.execute.finished"
    )
    assert finished.payload["outcome"] == "evicted"
    assert finished.payload["elapsed_ms"] >= 0.0
    assert "model.load.initiated" in signals
    assert "model.load.completed" in signals
