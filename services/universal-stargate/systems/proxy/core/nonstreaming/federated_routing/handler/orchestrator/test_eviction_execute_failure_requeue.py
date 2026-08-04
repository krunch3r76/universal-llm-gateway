"""INV-R regressions: eviction execute failure requeue and wait continuation modes."""

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
from universal_protocol import ErrorCode  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    admission as admission_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    eviction_execution as eviction_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    load_and_finalize as load_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (  # noqa: E402
    continuation_still_transient,
)
from systems.routing.selection.decision.types import (  # noqa: E402
    ConstraintFailure,
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
    def __init__(self) -> None:
        self.pause_calls: list[tuple[str, float, str]] = []
        self.acquire_calls: list[Any] = []

    def pause_admission(
        self, routing_key: str, *, duration_s: float, reason: str
    ) -> None:
        self.pause_calls.append((routing_key, duration_s, reason))

    async def acquire_token(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> _FakeToken:
        self.acquire_calls.append((request_id, model_id, allowed_gateway_ids))
        return _FakeToken(gateway_id=next(iter(allowed_gateway_ids)))


class _FakeFederatedManager:
    def __init__(self, gateways: list[Any] | None = None) -> None:
        self._gateways = gateways or []

    def get_all_gateways(self) -> list[Any]:
        return self._gateways

    def get_state_version(self) -> int:
        return 1

    async def wait_for_state_change(self, _version: int, _timeout: float) -> None:
        return None

    def mark_loading_optimistic(self, _gateway_id: str, _model_id: Any) -> bool:
        return False

    def clear_model_loading_optimistic(self, _gateway_id: str, _model_id: Any) -> None:
        return None


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


def _plan() -> EvictionPlanSummary:
    return EvictionPlanSummary(
        models_to_evict=frozenset({GEMMA}),
        freed_vram_mb=12_000,
        freed_ram_mb=0,
        estimated_cost=-50.0,
    )


def _trace(
    *,
    plan: EvictionPlanSummary | None = None,
    constraints: tuple[ConstraintFailure, ...] = (),
) -> DecisionTrace:
    return DecisionTrace(
        model_id=str(TARGET),
        original_model_id=None,
        request_id="req-eviction-requeue",
        candidates=(
            GatewayCandidate(
                gateway=_gateway(),
                tier=FeasibilityTier.T2_FEASIBLE_EVICT,
                eviction_plan=plan or _plan(),
                constraints_failed=constraints,
            ),
        ),
        selection_tier=FeasibilityTier.T2_FEASIBLE_EVICT,
        selection_reason="test",
    )


def test_execution_failure_continuation_without_busy_block() -> None:
    trace = _trace(
        constraints=(
            ConstraintFailure(
                constraint="compute_type_capacity",
                reason="at cap",
                details={"retryable": True},
            ),
        )
    )
    assert continuation_still_transient(trace, mode="execution_failure") is True
    assert continuation_still_transient(trace, mode="busy_block") is False


def test_busy_block_mode_unchanged() -> None:
    trace = _trace(
        constraints=(
            ConstraintFailure(
                constraint="eviction_blocked_by_busy_models",
                reason="busy",
                details={"retryable": True},
            ),
        )
    )
    assert continuation_still_transient(trace, mode="busy_block") is True
    assert continuation_still_transient(trace, mode="transient_capacity") is True


def test_permanent_resource_not_transient_continuation() -> None:
    trace = _trace(
        constraints=(
            ConstraintFailure(
                constraint="has_enough_vram",
                reason="short",
                details={},
            ),
            ConstraintFailure(
                constraint="can_fit_with_eviction",
                reason="no reclaimable VRAM",
                details={"retryable": False},
            ),
        )
    )
    assert continuation_still_transient(trace, mode="execution_failure") is False
    assert continuation_still_transient(trace, mode="transient_capacity") is False


@pytest.mark.asyncio
async def test_execution_failed_publishes_event_and_requeues_without_client_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_bus = _FakeEventBus()
    load_orchestrator = _FakeLoadOrchestrator()
    pool = _FakeCapacityPool()
    token = _FakeToken("edge-jupiter-gateway")
    context = SimpleNamespace(
        request_id="req-exec-fail",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=token,
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() + 60.0,
    )
    reselected = _gateway()
    wait_mock = AsyncMock(return_value=(reselected, _trace(), 42))
    exec_calls = {"n": 0}

    async def _exec_toggle(**kwargs: Any) -> Any:
        exec_calls["n"] += 1
        if exec_calls["n"] == 1:
            return eviction_mod.MasterEvictionResult(
                outcome=eviction_mod.MasterEvictionOutcome.EXECUTION_FAILED,
                gateway_id="edge-jupiter-gateway",
                requester="req-exec-fail",
            )
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EVICTED,
            gateway_id="edge-jupiter-gateway",
            requester="req-exec-fail",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _exec_toggle,
    )
    monkeypatch.setattr(load_mod, "_wait_and_retry_selection", wait_mock)
    admission_mock = AsyncMock(return_value=reselected)
    monkeypatch.setattr(admission_mod, "acquire_admission_token", admission_mock)
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [reselected],
    )

    fed_gw = SimpleNamespace(
        gateway_id="edge-jupiter-gateway",
        dispatchable=True,
        available_models=frozenset({TARGET, GEMMA}),
    )

    await load_mod.finalize_selection_and_load(
        context=context,
        selected_gateway=_gateway(),
        trace=_trace(),
        event_bus=event_bus,
        federated_manager=_FakeFederatedManager([fed_gw]),
        federated_load_orchestrator=load_orchestrator,
        federation_forwarder=_FakeForwarder(),
        routing_config={"drain_duration_s": 30.0},
        decision_engine=SimpleNamespace(),
        placement=SimpleNamespace(model_id=TARGET),
        stability_tracker=SimpleNamespace(get_current_best=lambda _m: None),
        routing_start_time=time.time(),
        eviction_cooldown_s=120.0,
        capacity_pool=pool,
    )

    execute_failed = [
        e for e in event_bus.events if e.signal == "routing.eviction.execute.failed"
    ]
    assert len(execute_failed) == 1
    assert token.released is True
    assert context.capacity_token is None
    wait_mock.assert_awaited_once()
    assert wait_mock.await_args.kwargs["continuation_mode"] == "execution_failure"
    admission_mock.assert_awaited_once()
    assert pool.pause_calls[0] == (
        GEMMA.routing_key,
        30.0,
        "eviction_execute_victim_pin",
    )
    assert exec_calls["n"] == 2
    assert len(load_orchestrator.load_calls) == 1


@pytest.mark.asyncio
async def test_execution_failed_deadline_exhaustion_raises_capacity_not_eviction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        request_id="req-deadline",
        selected_model=TARGET,
        model_sticky=False,
        capacity_token=_FakeToken("edge-jupiter-gateway"),
        selected_gateway=None,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=time.monotonic() + 5.0,
    )

    async def _exec_failed(**kwargs: Any) -> Any:
        return eviction_mod.MasterEvictionResult(
            outcome=eviction_mod.MasterEvictionOutcome.EXECUTION_FAILED,
            gateway_id="edge-jupiter-gateway",
            requester="req-deadline",
        )

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.eviction_execution.execute_master_eviction",
        _exec_failed,
    )
    monkeypatch.setattr(
        load_mod,
        "_wait_and_retry_selection",
        AsyncMock(return_value=(None, _trace(), 5000)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await load_mod.finalize_selection_and_load(
            context=context,
            selected_gateway=_gateway(),
            trace=_trace(),
            event_bus=_FakeEventBus(),
            federated_manager=_FakeFederatedManager([]),
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

    detail = exc_info.value.detail
    assert detail["code"] == ErrorCode.STICKY_CAPACITY
    assert detail["code"] != ErrorCode.EVICTION_FAILED


@pytest.mark.asyncio
async def test_empty_healthy_after_startup_raises_gateway_disconnected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.federation.common.config.schema import EndpointCategory
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        selection as selection_mod,
    )

    class _Manager:
        def get_all_gateways(self) -> list[Any]:
            return []

        def get_healthy_gateways(self) -> list[Any]:
            return []

        uptime_s = 1000.0

    async def _no_wait(**kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(selection_mod, "wait_for_startup_gateway", _no_wait)
    context = SimpleNamespace(
        request_id="req-no-gw",
        selected_model=TARGET,
        model_sticky=False,
        excluded_gateway_ids=set(),
        routing_endpoint_category=EndpointCategory.GENERATION,
        http_request=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await selection_mod.run_initial_selection(
            context=context,
            federated_manager=_Manager(),
            federated_load_orchestrator=None,
            event_bus=None,
            routing_config={"request_queue": {"startup_queue_timeout_s": 180.0}},
            stability_tracker=SimpleNamespace(),
            routing_key_tracker=None,
            capacity_pool=None,
            circuit_breaker=None,
        )

    detail = exc_info.value.detail
    assert detail["code"] == ErrorCode.GATEWAY_DISCONNECTED
    assert detail["retryable"] is True


@pytest.mark.asyncio
async def test_model_absent_from_all_catalogs_raises_model_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.federation.common.config.schema import EndpointCategory
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        selection as selection_mod,
    )

    healthy = SimpleNamespace(
        gateway_id="edge-jupiter-gateway",
        dispatchable=True,
        available_models=frozenset({GEMMA}),
        loaded_models=frozenset(),
        model_resources={},
        heartbeat_age_ms=0,
        telemetry_age_ms=0,
        is_unreachable=False,
    )

    class _Manager:
        def get_all_gateways(self) -> list[Any]:
            return [healthy]

        def get_healthy_gateways(self) -> list[Any]:
            return [healthy]

        uptime_s = 5000.0

        def is_inference_banned(self, _gateway: str, _model: Any) -> bool:
            return False

    context = SimpleNamespace(
        request_id="req-missing-model",
        selected_model=TARGET,
        model_sticky=False,
        excluded_gateway_ids=set(),
        routing_endpoint_category=EndpointCategory.GENERATION,
        http_request=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await selection_mod.run_initial_selection(
            context=context,
            federated_manager=_Manager(),
            federated_load_orchestrator=None,
            event_bus=None,
            routing_config={},
            stability_tracker=SimpleNamespace(),
            routing_key_tracker=None,
            capacity_pool=None,
            circuit_breaker=None,
        )

    detail = exc_info.value.detail
    assert detail["code"] == ErrorCode.MODEL_NOT_FOUND
    assert detail["retryable"] is False
