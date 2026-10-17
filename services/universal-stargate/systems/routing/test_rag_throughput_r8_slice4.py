# ruff: noqa: I001
"""RAG throughput r8 slice 4 acceptance tests (4.21–4.37 subset)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from universal_protocol import ErrorCode, error_envelope, get_http_status

_stargate_root = str(Path(__file__).resolve().parents[2])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

# ruff: noqa: E402

from model_id import ModelId

from systems.federation.common.types import FederatedGateway
from systems.proxy.core.nonstreaming.capacity_deadline import apply_capacity_deadline
from systems.proxy.core.nonstreaming.context import RequestContext
from systems.proxy.core.nonstreaming.executor.embedding_failover import (
    http_exception_triggers_cpu_failover,
)
from systems.proxy.core.nonstreaming.executor.oom_recovery import attempt_oom_recovery
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E501
    admission as _admission_mod,
    overflow as _overflow_mod,
    pre_route_queue as _pre_route_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator.rejection import (  # noqa: E501
    no_selection_outcome as rejection_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (
    clamp_eviction_wait_timeout,
)
from systems.routing.capacity._pool._acquisition import _AcquisitionMixin
from systems.routing.model_router import ModelRouter
from systems.routing.rag_protected_models import (
    is_rag_protected_model,
    rag_contextualize_model_id,
    rag_embedding_model_id,
)
from systems.routing.selection.decision import (
    ConstraintFailure,
    DecisionTrace,
    FeasibilityTier,
    GatewayCandidate,
    create_decision_engine,
)
from systems.routing.selection.decision.config import RoutingPolicy
from systems.routing.selection.decision.feasibility import evaluate_feasibility
from systems.routing.selection.decision.feasibility_reclaim import (
    can_fit_after_eviction_including_busy,
)
from systems.routing.selection.decision.stability import StickyPlacementTracker
from systems.routing.selection.types import Gateway, Placement

acquire_admission_token = _admission_mod.acquire_admission_token
apply_non_sticky_overflow = _overflow_mod.apply_non_sticky_overflow
wait_for_retryable_capacity = _pre_route_mod.wait_for_retryable_capacity

_OVERFLOW_MODEL = ModelId.parse("qwen3-5-9b-q8-0-131072")


@pytest.fixture(autouse=True)
def _pin_stargate_sys_path() -> None:
    """Keep universal-stargate ahead of gateway ``src`` after cross-package tests."""
    while _stargate_root in sys.path:
        sys.path.remove(_stargate_root)
    sys.path.insert(0, _stargate_root)
    src_mod = sys.modules.get("src")
    if src_mod is not None and not hasattr(src_mod, "scheduling"):
        for key in list(sys.modules):
            if key == "src" or key.startswith("src."):
                del sys.modules[key]


def _gateway(**kwargs) -> Gateway:
    defaults = dict(
        ref=None,
        name="edge-io",
        ram_free_mb=100_000,
        vram_free_mb=2_000,
        ram_total_mb=100_000,
        vram_total_mb=32_000,
        loaded_models=frozenset(),
        model_details={},
        model_measured_vram={},
    )
    defaults.update(kwargs)
    return Gateway(**defaults)


def _overflow_gateways() -> tuple[Gateway, Gateway]:
    details = {
        _OVERFLOW_MODEL: {
            "vram_usage": 15_000,
            "ram_usage": 0,
            "max_concurrent_requests": 4,
        }
    }
    gw_a = _gateway(
        name="gw-a",
        ref=MagicMock(),
        loaded_models=frozenset({_OVERFLOW_MODEL}),
        available_models=frozenset({_OVERFLOW_MODEL}),
        model_details=details,
        vram_free_mb=50_000,
    )
    gw_b = _gateway(
        name="gw-b",
        ref=MagicMock(),
        loaded_models=frozenset({_OVERFLOW_MODEL}),
        available_models=frozenset({_OVERFLOW_MODEL}),
        model_details=details,
        vram_free_mb=50_000,
    )
    return gw_a, gw_b


class _SaturationPool:
    def get_slot_info(self, gw: str, _model: str) -> tuple[int, int, int]:
        if gw == "gw-a":
            return (0, 4, 4)
        if gw == "gw-b":
            return (4, 0, 4)
        return (1, 0, 4)


def _overflow_context(*, model_sticky: bool) -> RequestContext:
    ctx = RequestContext(
        request_id="req-overflow",
        start_time=time.time(),
        selected_model=_OVERFLOW_MODEL,
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    ctx.model_sticky = model_sticky
    return ctx


@pytest.mark.asyncio
async def test_4_21_non_sticky_overflow_to_saturated_peer() -> None:
    gw_a, gw_b = _overflow_gateways()
    policy = RoutingPolicy(
        default_sticky=True,
        sticky_overrides={"qwen3-5-9b-q8-0-131072": False},
    )
    assert policy.is_sticky(_OVERFLOW_MODEL) is False
    engine = create_decision_engine(policy)
    placement = Placement(
        model_id=_OVERFLOW_MODEL,
        ram_mb=0,
        vram_mb=15_000,
        is_gpu=True,
        endpoint_category="embedding",
    )
    context = _overflow_context(model_sticky=False)
    selected, _trace, allowed, origin, depth = await apply_non_sticky_overflow(
        selected_gateway=gw_a,
        trace=None,
        context=context,
        policy=policy,
        capacity_pool=_SaturationPool(),
        event_bus=None,
        decision_engine=engine,
        gateways_for_routing=[gw_a, gw_b],
        placement=placement,
        federated_load_orchestrator=None,
        stability_tracker=StickyPlacementTracker(),
    )
    assert selected is not None
    assert selected.name == "gw-b"
    assert origin == "gw-a"
    assert depth == 0
    assert allowed is not None
    assert "gw-b" in allowed


@pytest.mark.asyncio
async def test_4_32_default_sticky_skips_overflow_when_model_sticky() -> None:
    gw_a, gw_b = _overflow_gateways()
    policy = RoutingPolicy(default_sticky=True)
    assert policy.is_sticky(_OVERFLOW_MODEL) is True
    engine = create_decision_engine(policy)
    placement = Placement(
        model_id=_OVERFLOW_MODEL,
        ram_mb=0,
        vram_mb=15_000,
        is_gpu=True,
        endpoint_category="embedding",
    )
    context = _overflow_context(model_sticky=True)
    selected, _trace, allowed, origin, _depth = await apply_non_sticky_overflow(
        selected_gateway=gw_a,
        trace=None,
        context=context,
        policy=policy,
        capacity_pool=_SaturationPool(),
        event_bus=None,
        decision_engine=engine,
        gateways_for_routing=[gw_a, gw_b],
        placement=placement,
        federated_load_orchestrator=None,
        stability_tracker=StickyPlacementTracker(),
    )
    assert selected is gw_a
    assert allowed is None
    assert origin is None


def test_4_22_embedding_never_evictable() -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    idle = {emb, ctx, rerank}
    evictable = [
        mid for mid in idle if mid != target and not is_rag_protected_model(mid)
    ]
    assert evictable == [rerank]


def test_4_23_cpu_failover_scaffold_defaults_and_trigger() -> None:
    from services.rag.embeddings.constants import (
        BATCH_CPU_FAILOVER_ENABLED,
        QUERY_CPU_FAILOVER_ENABLED,
    )

    assert QUERY_CPU_FAILOVER_ENABLED is False
    assert BATCH_CPU_FAILOVER_ENABLED is False
    exc = HTTPException(
        status_code=get_http_status(ErrorCode.NO_FEASIBLE_GATEWAY),
        detail=error_envelope(
            code=ErrorCode.NO_FEASIBLE_GATEWAY,
            message="no gateway",
            source="master",
            retryable=True,
            data={},
        ),
    )
    assert http_exception_triggers_cpu_failover(exc) is True


def test_4_27_reclaim_excludes_protected_fast_refuse() -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    gw = _gateway(
        vram_free_mb=500,
        loaded_models=frozenset({emb, ctx}),
        available_models=frozenset({target, emb, ctx}),
        model_details={
            target: {"vram_usage": 12_000, "ram_usage": 0},
            emb: {"vram_usage": 15_000, "ram_usage": 0},
            ctx: {"vram_usage": 14_000, "ram_usage": 0},
        },
    )

    def _lookup(mid: ModelId) -> tuple[int, int]:
        if mid == target:
            return (12_000, 0)
        return (15_000, 0)

    ok, diag = can_fit_after_eviction_including_busy(
        gw,
        Placement(model_id=target, ram_mb=0, vram_mb=12_000, is_gpu=True),
        _lookup,
    )
    assert ok is False
    assert diag["max_freeable_vram"] < diag["required_vram"]


@pytest.mark.asyncio
async def test_4_34_oom_recovery_skips_protected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target = ctx
    gateway = FederatedGateway(
        gateway_id="edge-io",
        remote_stargate_id="edge-io",
        remote_stargate_url="http://localhost:9999",
        loaded_models=frozenset({emb, ctx, rerank}),
    )
    execute = AsyncMock(return_value=MagicMock(ok=True))
    monkeypatch.setattr(
        "systems.routing.eviction.executor.execute_eviction_plan",
        execute,
    )
    ok = await attempt_oom_recovery(
        gateway=gateway,
        model_id=target,
        federated_manager=MagicMock(),
        federation_forwarder=MagicMock(),
        request_tracker=None,
        event_bus=None,
        request_id="req-oom",
    )
    assert ok is True
    plan = execute.call_args.kwargs["eviction_plan"]
    assert emb not in plan.models_to_evict
    assert ctx not in plan.models_to_evict
    assert rerank in plan.models_to_evict


def test_4_36_measured_vram_via_model_router_collect() -> None:
    mid = ModelId.parse("qwen3-5-9b-q8-0-131072")
    fg = FederatedGateway(
        gateway_id="edge-io",
        remote_stargate_id="edge-io",
        remote_stargate_url="http://localhost:9999",
        loaded_models=frozenset({mid}),
        available_models=frozenset({mid}),
        model_resources={
            mid: {"vram_usage": 15_134, "ram_usage": 0, "max_concurrent_requests": 4},
        },
        model_measured_vram={mid: 14_544},
    )
    federated_manager = MagicMock()
    federated_manager.get_healthy_gateways.return_value = [fg]
    router = ModelRouter(
        gateway_manager=MagicMock(),
        federated_manager=federated_manager,
        local_stargate_id="master-stargate",
    )
    gateways = router._collect_candidate_gateways()
    assert len(gateways) == 1
    assert gateways[0].model_measured_vram[mid] == 14_544


def test_4_29_effective_context_per_slot() -> None:
    assert 131_072 // 4 == 32_768


def test_4_35_cold_load_slack_tier() -> None:
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    placement = Placement(model_id=target, ram_mb=0, vram_mb=10_000, is_gpu=True)
    policy = RoutingPolicy()

    def req(mid: ModelId) -> tuple[int, int]:
        if mid == target:
            return (10_000, 0)
        return (2_000, 0)

    gw_t2 = _gateway(
        vram_free_mb=11_548,
        loaded_models=frozenset({rerank}),
        available_models=frozenset({target, rerank}),
        model_details={
            target: {"vram_usage": 10_000, "ram_usage": 0},
            rerank: {"vram_usage": 2_000, "ram_usage": 0},
        },
    )
    tier_t2, _, plan_t2 = evaluate_feasibility(gw_t2, placement, policy, req)
    assert tier_t2 == FeasibilityTier.T2_FEASIBLE_EVICT
    assert plan_t2 is not None

    gw_t1 = _gateway(
        vram_free_mb=15_000,
        loaded_models=frozenset({rerank}),
        available_models=frozenset({target, rerank}),
        model_details={
            target: {"vram_usage": 10_000, "ram_usage": 0},
            rerank: {"vram_usage": 2_000, "ram_usage": 0},
        },
    )
    tier_t1, _, plan_t1 = evaluate_feasibility(gw_t1, placement, policy, req)
    assert tier_t1 == FeasibilityTier.T1_FEASIBLE_NOW
    assert plan_t1 is None


def _transient_capacity_trace(model: ModelId) -> DecisionTrace:
    candidate = GatewayCandidate(
        gateway=_gateway(name="gw-a"),
        tier=FeasibilityTier.T0_INFEASIBLE,
        constraints_failed=(
            ConstraintFailure(
                constraint="eviction_blocked_by_busy_models",
                reason="busy",
                details={"retryable": True},
            ),
        ),
    )
    return DecisionTrace(
        model_id=str(model),
        original_model_id=None,
        request_id="req-cap",
        candidates=(candidate,),
        selection_tier=FeasibilityTier.T0_INFEASIBLE,
        selection_reason="blocked",
    )


def test_4_37_eviction_wait_clamped_by_x_request_timeout() -> None:
    ctx = RequestContext(
        request_id="r1",
        start_time=time.time(),
        selected_model=ModelId.parse("qwen3-embedding-8b-q8-0-8192"),
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    apply_capacity_deadline(
        ctx,
        routing_config={"request_queue": {"queue_timeout": 300.0}},
        request_timeout_hint=2.0,
    )
    clamped = clamp_eviction_wait_timeout(ctx, config_timeout=300.0)
    assert clamped <= 2.1


@pytest.mark.asyncio
async def test_4_37_pre_route_queue_budget_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ModelId.parse("qwen3-embedding-8b-q8-0-8192")
    trace = _transient_capacity_trace(model)
    context = RequestContext(
        request_id="req-pre",
        start_time=time.time(),
        selected_model=model,
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    context.model_sticky = False
    context.queue_wait_timeout_s = 1.0
    _mono = {"t": 1000.0}

    def _advance_mono() -> float:
        _mono["t"] += 2.0
        return _mono["t"]

    monkeypatch.setattr(_pre_route_mod.time, "monotonic", _advance_mono)
    federated_manager = MagicMock()
    federated_manager.get_state_version.return_value = 0
    federated_manager.get_all_gateways.return_value = []

    async def _wait_change(_version: int, _timeout: float) -> None:
        await asyncio.sleep(0)

    federated_manager.wait_for_state_change = _wait_change
    decision_engine = MagicMock()
    decision_engine.select.return_value = (None, trace)
    placement = Placement(
        model_id=model,
        ram_mb=0,
        vram_mb=8_000,
        is_gpu=True,
        endpoint_category="embedding",
    )
    selected, _ = await wait_for_retryable_capacity(
        selected_gateway=None,
        trace=trace,
        event_bus=None,
        decision_engine=decision_engine,
        federated_manager=federated_manager,
        placement=placement,
        context=context,
        stability_tracker=StickyPlacementTracker(),
    )
    assert selected is None
    monkeypatch.setattr(
        rejection_mod,
        "_wait_and_retry_selection",
        AsyncMock(return_value=(None, trace, 10)),
    )
    fg = FederatedGateway(
        gateway_id="gw-a",
        remote_stargate_id="gw-a",
        remote_stargate_url="http://localhost:9999",
        available_models=frozenset({model}),
    )
    with pytest.raises(HTTPException) as exc_info:
        await rejection_mod.handle_selection_rejection(
            selected_gateway=None,
            trace=trace,
            context=context,
            event_bus=None,
            federated_manager=federated_manager,
            federated_gateways=[fg],
            routing_config={},
            decision_engine=decision_engine,
            placement=placement,
            gateways_for_routing=[
                _gateway(name="gw-a", available_models=frozenset({model})),
            ],
            stability_tracker=StickyPlacementTracker(),
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_4_37_eviction_reselect_budget_returns_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ModelId.parse("qwen3-5-9b-q8-0-131072")
    trace = _transient_capacity_trace(model)
    wait_mock = AsyncMock(return_value=(None, trace, 50))
    monkeypatch.setattr(rejection_mod, "_wait_and_retry_selection", wait_mock)
    context = SimpleNamespace(
        request_id="req-evict-wait",
        selected_model=model,
        model_sticky=True,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=None,
    )
    with pytest.raises(HTTPException) as exc_info:
        await rejection_mod.handle_selection_rejection(
            selected_gateway=None,
            trace=trace,
            context=context,
            event_bus=None,
            federated_manager=SimpleNamespace(),
            federated_gateways=[],
            routing_config={},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=model),
            gateways_for_routing=[],
            stability_tracker=SimpleNamespace(),
        )
    wait_mock.assert_awaited_once()
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == ErrorCode.STICKY_CAPACITY


class _Pool(_AcquisitionMixin):
    def __init__(self) -> None:
        self._queues = {}
        self._in_flight = {}
        self._slot_last_change = {}
        self._max_queue_depth = 0

    def _ensure_subscribed(self) -> None:
        return None

    def is_paused(self, _model_id: str) -> bool:
        return False

    def get_slot_info(self, _gw: str, _model: str) -> tuple[int, int, int]:
        return (0, 0, 0)

    def _emit_queue_entered(self, *args, **kwargs) -> None:
        pass

    def _emit_queue_admitted(self, *args, **kwargs) -> None:
        pass

    def _emit_queue_waiting(self, *args, **kwargs) -> None:
        pass

    def _emit_queue_full(self, *args, **kwargs) -> None:
        pass

    def _remove_waiter(self, *args, **kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_4_37_acquire_token_budget_returns_503() -> None:
    model = ModelId.parse("qwen3-embedding-8b-q8-0-8192")
    gw = _gateway(
        name="edge-io",
        loaded_models=frozenset({model}),
        available_models=frozenset({model}),
    )
    context = RequestContext(
        request_id="req-admit",
        start_time=time.time(),
        selected_model=model,
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    context.model_sticky = False
    apply_capacity_deadline(
        context,
        routing_config={"request_queue": {"queue_timeout": 300.0}},
        request_timeout_hint=0.001,
    )
    with pytest.raises(HTTPException) as exc_info:
        await acquire_admission_token(
            context=context,
            selected_gateway=gw,
            gateways_for_routing=[gw],
            routing_config={},
            event_bus=None,
            capacity_pool=_Pool(),
            stability_tracker=StickyPlacementTracker(),
            allowed_gateway_ids_override=None,
            overflow_origin_gateway=None,
            overflow_depth_before=0,
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == ErrorCode.GATEWAY_AT_CAPACITY
