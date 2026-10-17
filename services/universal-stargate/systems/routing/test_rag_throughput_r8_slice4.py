# ruff: noqa: I001, E501
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
from systems.federation.common.config.schema import EndpointCategory
from systems.federation.common.protocol import FederationMessageType
from systems.federation.master.manager.federated_gateway_manager import (
    FederatedGatewayManager,
)
from systems.proxy.core.nonstreaming.executor.embedding_failover import (
    CPU_EMBEDDING_MODEL_ID,
    http_exception_triggers_cpu_failover,
)
from systems.proxy.core.nonstreaming.executor.embeddings import execute_embedding_request
from systems.routing.selection.decision.eviction_cooldown_policy import (
    EvictionRequestClass,
)
from systems.routing.selection.decision.eviction_planning import _compute_eviction_plan
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
def _clear_rag_protected_cache() -> None:
    from systems.routing.rag_protected_models import clear_rag_protected_cache

    clear_rag_protected_cache()
    yield
    clear_rag_protected_cache()


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


def test_4_22_compute_eviction_plan_excludes_rag_protected() -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")

    def req(mid: ModelId) -> tuple[int, int]:
        if mid == target:
            return (12_000, 0)
        if mid == emb:
            return (15_000, 0)
        if mid == ctx:
            return (14_000, 0)
        return (2_000, 0)

    gw = _gateway(
        name="edge-io",
        vram_free_mb=13_000,
        loaded_models=frozenset({emb, ctx, rerank}),
        available_models=frozenset({target, emb, ctx, rerank}),
        model_details={
            target: {"vram_usage": 12_000, "ram_usage": 0},
            emb: {"vram_usage": 15_000, "ram_usage": 0},
            ctx: {"vram_usage": 14_000, "ram_usage": 0},
            rerank: {"vram_usage": 2_000, "ram_usage": 0},
        },
    )
    plan = _compute_eviction_plan(
        gw,
        Placement(model_id=target, ram_mb=0, vram_mb=12_000, is_gpu=True),
        req,
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    from systems.routing.selection.decision.types import EvictionPlanSummary

    assert isinstance(plan, EvictionPlanSummary)
    assert emb not in plan.models_to_evict
    assert ctx not in plan.models_to_evict
    assert rerank in plan.models_to_evict


def test_4_22_protected_only_loaded_no_eviction_plan() -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")

    def req(mid: ModelId) -> tuple[int, int]:
        if mid == target:
            return (12_000, 0)
        return (15_000, 0)

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
    plan = _compute_eviction_plan(
        gw,
        Placement(model_id=target, ram_mb=0, vram_mb=12_000, is_gpu=True),
        req,
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    from systems.routing.selection.decision.types import EvictionPlanSummary

    assert not isinstance(plan, EvictionPlanSummary)


def test_4_22_without_protection_evicts_protected_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")

    def req(mid: ModelId) -> tuple[int, int]:
        if mid == target:
            return (12_000, 0)
        return (15_000, 0)

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
    from systems.routing.selection.decision.types import EvictionPlanSummary

    protected_plan = _compute_eviction_plan(
        gw,
        Placement(model_id=target, ram_mb=0, vram_mb=12_000, is_gpu=True),
        req,
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    assert not isinstance(protected_plan, EvictionPlanSummary)

    monkeypatch.setattr(
        "systems.routing.selection.decision.eviction_planning.is_rag_protected_model",
        lambda _m: False,
    )
    plan = _compute_eviction_plan(
        gw,
        Placement(model_id=target, ram_mb=0, vram_mb=12_000, is_gpu=True),
        req,
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    assert isinstance(plan, EvictionPlanSummary)
    assert emb in plan.models_to_evict or ctx in plan.models_to_evict
    assert isinstance(plan, EvictionPlanSummary) ^ isinstance(
        protected_plan, EvictionPlanSummary
    )


@pytest.mark.asyncio
async def test_k3_embedding_failover_releases_token_when_no_gateway() -> None:
    from systems.proxy.core.nonstreaming.executor.embedding_failover import (
        run_embedding_with_cpu_failover,
    )

    gpu_model = ModelId.parse("qwen3-embedding-8b-q8-0-8192")
    context = RequestContext(
        request_id="req-no-gw",
        start_time=time.time(),
        selected_model=gpu_model,
        original_request={"input": ["x"]},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    release = AsyncMock()

    async def select(_ctx: RequestContext) -> None:
        _ctx.selected_gateway = None

    with pytest.raises(HTTPException):
        await run_embedding_with_cpu_failover(
            context=context,
            parsed_model_id=gpu_model,
            request_body={"input": ["x"]},
            resolved_request_id="req-no-gw",
            select_gateway_fn=select,
            forward_with_retry_fn=AsyncMock(),
            forward_embedding_fn=AsyncMock(),
            release_capacity_token_fn=release,
            emit_execution_completed_fn=AsyncMock(),
            event_bus=MagicMock(),
            oom_recovery_fn=None,
            cpu_failover_enabled=True,
        )
    assert release.await_count >= 1


def test_4_23_cpu_failover_constants_and_immediate_trigger() -> None:
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


def _gpu_no_feasible_exc() -> HTTPException:
    return HTTPException(
        status_code=get_http_status(ErrorCode.NO_FEASIBLE_GATEWAY),
        detail=error_envelope(
            code=ErrorCode.NO_FEASIBLE_GATEWAY,
            message="no gateway",
            source="master",
            retryable=True,
            data={},
        ),
    )


def _gateway_at_capacity_exc() -> HTTPException:
    return HTTPException(
        status_code=get_http_status(ErrorCode.GATEWAY_AT_CAPACITY),
        detail=error_envelope(
            code=ErrorCode.GATEWAY_AT_CAPACITY,
            message="full",
            source="master",
            retryable=True,
            data={},
        ),
    )


@pytest.mark.asyncio
async def test_4_23_execute_embedding_no_feasible_failover_when_enabled() -> None:
    gpu_model = "qwen3-embedding-8b-q8-0-8192"
    select_calls: list[str] = []

    async def select(ctx: RequestContext) -> None:
        select_calls.append(str(ctx.selected_model))
        if ctx.selected_model.routing_key == ModelId.parse(gpu_model).routing_key:
            raise _gpu_no_feasible_exc()
        fg = FederatedGateway(
            gateway_id="cpu-gw",
            remote_stargate_id="cpu-gw",
            remote_stargate_url="http://localhost:9999",
        )
        ctx.selected_gateway = _gateway(name="cpu-gw", ref=fg)

    async def forward(**kwargs) -> dict:
        return {"object": "list", "data": []}

    result = await execute_embedding_request(
        gpu_model,
        {"input": ["x"]},
        "req-embed-cpu",
        select_gateway_fn=select,
        release_routing_key_fn=lambda _rid: None,
        release_capacity_token_fn=AsyncMock(),
        forward_embedding_fn=forward,
        event_bus=MagicMock(),
        query_cpu_failover_enabled=True,
        batch_cpu_failover_enabled=True,
    )
    assert result["object"] == "list"
    assert CPU_EMBEDDING_MODEL_ID in select_calls


@pytest.mark.asyncio
async def test_4_23_execute_embedding_no_cpu_when_flag_off() -> None:
    gpu_model = "qwen3-embedding-8b-q8-0-8192"

    async def select(_ctx: RequestContext) -> None:
        raise _gpu_no_feasible_exc()

    with pytest.raises(HTTPException) as exc_info:
        await execute_embedding_request(
            gpu_model,
            {"input": ["x"]},
            "req-no-cpu",
            select_gateway_fn=select,
            release_routing_key_fn=lambda _rid: None,
            release_capacity_token_fn=AsyncMock(),
            forward_embedding_fn=AsyncMock(),
            event_bus=MagicMock(),
            query_cpu_failover_enabled=False,
            batch_cpu_failover_enabled=False,
        )
    assert exc_info.value.detail["code"] == ErrorCode.NO_FEASIBLE_GATEWAY


@pytest.mark.asyncio
async def test_4_23_execute_embedding_gpu_success_skips_cpu() -> None:
    gpu_model = "qwen3-embedding-8b-q8-0-8192"
    select_calls: list[str] = []

    async def select(ctx: RequestContext) -> None:
        select_calls.append(str(ctx.selected_model))
        fg = FederatedGateway(
            gateway_id="gpu-gw",
            remote_stargate_id="gpu-gw",
            remote_stargate_url="http://localhost:9999",
        )
        ctx.selected_gateway = _gateway(name="gpu-gw", ref=fg)

    await execute_embedding_request(
        gpu_model,
        {"input": ["x"]},
        "req-gpu-only",
        select_gateway_fn=select,
        release_routing_key_fn=lambda _rid: None,
        release_capacity_token_fn=AsyncMock(),
        forward_embedding_fn=AsyncMock(return_value={"object": "list", "data": []}),
        event_bus=MagicMock(),
        query_cpu_failover_enabled=True,
        batch_cpu_failover_enabled=True,
    )
    assert CPU_EMBEDDING_MODEL_ID not in select_calls


@pytest.mark.asyncio
async def test_4_23_gateway_at_capacity_no_cpu_until_budget_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gpu_model = "qwen3-embedding-8b-q8-0-8192"
    select_calls: list[str] = []
    mono = {"t": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: mono["t"])

    async def select(ctx: RequestContext) -> None:
        select_calls.append(str(ctx.selected_model))
        raise _gateway_at_capacity_exc()

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    with pytest.raises(HTTPException):
        await execute_embedding_request(
            gpu_model,
            {"input": ["x"]},
            "req-at-cap",
            select_gateway_fn=select,
            release_routing_key_fn=lambda _rid: None,
            release_capacity_token_fn=AsyncMock(),
            forward_embedding_fn=AsyncMock(),
            event_bus=event_bus,
            query_cpu_failover_enabled=True,
            batch_cpu_failover_enabled=True,
            request_timeout_hint=60.0,
        )
    assert CPU_EMBEDDING_MODEL_ID not in select_calls

    select_calls.clear()
    mono_seq = iter([5000.0, 5000.01, 5000.01, 5000.01, 5000.01])
    monkeypatch.setattr(time, "monotonic", lambda: next(mono_seq, 5000.01))

    async def select_expired(ctx: RequestContext) -> None:
        select_calls.append(str(ctx.selected_model))
        if ctx.selected_model.routing_key == ModelId.parse(gpu_model).routing_key:
            raise _gateway_at_capacity_exc()
        fg = FederatedGateway(
            gateway_id="cpu-gw",
            remote_stargate_id="cpu-gw",
            remote_stargate_url="http://localhost:9999",
        )
        ctx.selected_gateway = _gateway(name="cpu-gw", ref=fg)

    await execute_embedding_request(
        gpu_model,
        {"input": ["x"]},
        "req-at-cap-expired",
        select_gateway_fn=select_expired,
        release_routing_key_fn=lambda _rid: None,
        release_capacity_token_fn=AsyncMock(),
        forward_embedding_fn=AsyncMock(return_value={"object": "list", "data": []}),
        event_bus=event_bus,
        query_cpu_failover_enabled=True,
        batch_cpu_failover_enabled=True,
        request_timeout_hint=0.001,
    )
    assert CPU_EMBEDDING_MODEL_ID in select_calls


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


@pytest.mark.asyncio
async def test_4_36_resource_update_measured_vram_in_reclaim_and_eviction() -> None:
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    remote = "edge-io"
    gateway_name = "edge-io-gateway"

    bus = MagicMock()
    bus.subscribe_async = MagicMock()
    bus.publish_nowait = AsyncMock()
    mgr = FederatedGatewayManager(bus)
    await mgr.start()
    try:
        source = {
            "stargate_id": remote,
            "gateway_id": gateway_name,
            "node_id": "node-1",
        }
        catalog = {
            str(target): {"vram_usage": 500, "ram_usage": 0},
            str(rerank): {"vram_usage": 8_000, "ram_usage": 0},
        }
        await mgr.update_from_event(
            remote,
            FederationMessageType.GATEWAY_SNAPSHOT.value,
            {
                "source": source,
                "available_models": [str(target), str(rerank)],
                "loaded_models": [str(rerank)],
                "available_vram_mb": 500,
                "available_ram_mb": 32_000,
                "total_vram_mb": 32_000,
                "total_ram_mb": 64_000,
                "model_resources": catalog,
            },
        )
        await mgr.update_from_event(
            remote,
            FederationMessageType.RESOURCE_UPDATE.value,
            {
                "source": source,
                "available_vram_mb": 500,
                "available_ram_mb": 32_000,
                "total_vram_mb": 32_000,
                "total_ram_mb": 64_000,
                "model_vram": {str(rerank): 2_500},
            },
        )
        router = ModelRouter(
            gateway_manager=MagicMock(),
            federated_manager=mgr,
            local_stargate_id="master-stargate",
        )
        gateways = router._collect_candidate_gateways()
        assert len(gateways) == 1
        gw = gateways[0]
        loaded_rerank = next(
            m for m in gw.loaded_models if m.routing_key == rerank.routing_key
        )
        assert gw.model_measured_vram[loaded_rerank] == 2_500

        def req(mid: ModelId) -> tuple[int, int]:
            if mid == target:
                return (500, 0)
            return (8_000, 0)

        placement = Placement(model_id=target, ram_mb=0, vram_mb=500, is_gpu=True)
        _, diag = can_fit_after_eviction_including_busy(gw, placement, req)
        assert diag["max_freeable_vram"] == 500 + 2_500
        assert diag["max_freeable_vram"] < 500 + 8_000

        from systems.routing.selection.decision.types import EvictionPlanSummary

        plan = _compute_eviction_plan(
            gw,
            placement,
            req,
            eviction_cooldown_s=0.0,
            eviction_request_class=EvictionRequestClass.REQUIRED,
        )
        assert isinstance(plan, EvictionPlanSummary)
        assert loaded_rerank in plan.models_to_evict
        assert plan.catalog_freed_vram_mb == 2_500
    finally:
        await mgr.stop()


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


def test_4_37_eviction_wait_clamped_by_real_capacity_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = RequestContext(
        request_id="r1",
        start_time=time.time(),
        selected_model=ModelId.parse("qwen3-embedding-8b-q8-0-8192"),
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    mono = {"v": 1000.0}

    def fake_monotonic() -> float:
        return mono["v"]

    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.wait_continuation.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.capacity_deadline.time.monotonic",
        fake_monotonic,
    )
    apply_capacity_deadline(
        ctx,
        routing_config={"request_queue": {"queue_timeout": 300.0}},
        request_timeout_hint=2.0,
    )
    assert ctx._capacity_deadline_mono == pytest.approx(1002.0)
    mono["v"] = 1001.5
    clamped = clamp_eviction_wait_timeout(ctx, config_timeout=300.0)
    assert clamped == pytest.approx(0.5, abs=0.01)
    mono["v"] = 1003.0
    clamped_expired = clamp_eviction_wait_timeout(ctx, config_timeout=300.0)
    assert clamped_expired == 0.0


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
    context.queue_wait_timeout_s = 10.0
    context._capacity_deadline_mono = 1003.0
    wait_timeouts: list[float] = []
    _mono = {"t": 1000.0}

    monkeypatch.setattr(_pre_route_mod.time, "monotonic", lambda: _mono["t"])
    federated_manager = MagicMock()
    federated_manager.get_state_version.return_value = 0
    federated_manager.get_all_gateways.return_value = []

    async def _wait_change(_version: int, timeout: float) -> None:
        wait_timeouts.append(timeout)
        _mono["t"] += 3.5
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
    assert wait_timeouts
    assert wait_timeouts[0] == pytest.approx(3.0, abs=0.05)
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
    mono = {"t": 1000.0}
    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.wait_continuation.time.monotonic",
        lambda: mono["t"],
    )
    context = SimpleNamespace(
        request_id="req-evict-wait",
        selected_model=model,
        model_sticky=True,
        excluded_gateway_ids=set(),
        _capacity_deadline_mono=1002.0,
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
    assert wait_mock.await_args.kwargs["timeout_s"] == pytest.approx(2.0, abs=0.01)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == ErrorCode.STICKY_CAPACITY


@pytest.mark.asyncio
async def test_4_37_eviction_requeue_clamps_wait_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (
        eviction_requeue,
        load_and_finalize,
    )

    model = ModelId.parse("qwen3-5-9b-q8-0-131072")
    wait_mock = AsyncMock(return_value=(None, SimpleNamespace(), 50))
    monkeypatch.setattr(eviction_requeue, "_wait_and_retry_selection", wait_mock)
    monkeypatch.setattr(
        load_and_finalize,
        "finalize_selection_and_load",
        AsyncMock(return_value=("gw", None)),
    )
    mono = {"t": 1000.0}
    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.federated_routing.wait_continuation.time.monotonic",
        lambda: mono["t"],
    )
    context = SimpleNamespace(
        capacity_token=None,
        selected_model=model,
        _capacity_deadline_mono=1002.0,
        excluded_gateway_ids=set(),
    )
    with pytest.raises(HTTPException) as exc_info:
        await eviction_requeue.requeue_after_transient_eviction(
            context=context,
            federated_manager=SimpleNamespace(get_all_gateways=lambda: []),
            federated_load_orchestrator=None,
            federation_forwarder=None,
            routing_config={"eviction_wait_timeout_s": 300.0},
            decision_engine=SimpleNamespace(),
            placement=SimpleNamespace(model_id=model),
            event_bus=None,
            stability_tracker=SimpleNamespace(),
            routing_start_time=0.0,
            eviction_cooldown_s=0.0,
            capacity_pool=None,
            routing_key_tracker=None,
            continuation_mode="transient_capacity",
            timeout_reason="test",
        )
    wait_mock.assert_awaited_once()
    assert wait_mock.await_args.kwargs["timeout_s"] == pytest.approx(2.0, abs=0.01)
    assert exc_info.value.status_code == 503


def test_4_4_embedding_capacity_acquire_deadline_mono_helper() -> None:
    from systems.proxy.core.nonstreaming.capacity_deadline import (
        embedding_capacity_acquire_deadline_mono,
    )

    model = ModelId.parse("qwen3-embedding-8b-q8-0-8192")
    emb_ctx = RequestContext(
        request_id="emb",
        start_time=time.time(),
        selected_model=model,
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    emb_ctx.routing_endpoint_category = EndpointCategory.EMBEDDING
    emb_ctx._capacity_deadline_mono = 12345.0
    assert embedding_capacity_acquire_deadline_mono(emb_ctx) == 12345.0

    chat_ctx = RequestContext(
        request_id="chat",
        start_time=time.time(),
        selected_model=model,
        original_request={},
        raw_client_fields={},
        user_params={},
        middleware_actions=[],
    )
    chat_ctx.routing_endpoint_category = EndpointCategory.GENERATION
    chat_ctx._capacity_deadline_mono = 99999.0
    assert embedding_capacity_acquire_deadline_mono(chat_ctx) is None


@pytest.mark.asyncio
async def test_4_4_model_router_acquire_unbounded_without_timeout() -> None:
    model = ModelId.parse("hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid")
    gw = _gateway(
        name="edge-io",
        loaded_models=frozenset({model}),
        available_models=frozenset({model}),
        model_details={model: {"max_concurrent_requests": 4, "vram_usage": 1000}},
    )

    class _RecordingPool:
        async def acquire_token(self, **kwargs):
            assert kwargs.get("timeout_s") is None
            from systems.routing.capacity.pool import CapacityToken

            return CapacityToken(
                gateway_id="edge-io",
                model_id=kwargs["model_id"],
                request_id=kwargs["request_id"],
                _pool=None,
            )

    from gateway_client.config import ModelMetadata

    gateway_manager = MagicMock()
    gateway_manager.fetch_model_configuration = AsyncMock(
        return_value=ModelMetadata(
            id=str(model),
            model_type="llm",
            input_schema="messages",
            parameter_defaults={},
            supported_parameters=[],
            middleware_config={},
            enabled=True,
            loader_type="llama",
            path="/models/x",
            ram_usage=0,
            vram_usage=1000,
        )
    )
    router = ModelRouter(
        gateway_manager=gateway_manager,
        federated_manager=MagicMock(get_healthy_gateways=MagicMock(return_value=[])),
        local_stargate_id="master-stargate",
        capacity_pool=_RecordingPool(),
    )
    router._routing_policy = RoutingPolicy(capacity_acquire_timeout_s=0.001)
    router._collect_candidate_gateways = MagicMock(return_value=[gw])  # type: ignore[method-assign]
    router._refresh_stale_candidates = AsyncMock(return_value=[gw])  # type: ignore[method-assign]
    router._select_and_record_stats = MagicMock(  # type: ignore[method-assign]
        return_value=(gw, None)
    )
    router._execute_local_eviction = AsyncMock(return_value=True)  # type: ignore[method-assign]

    selected = await router.route_request(
        {"model": str(model), "_request_id": "req-route", "sticky": False}
    )
    assert selected is gw


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
    from systems.federation.common.config.schema import EndpointCategory

    context.routing_endpoint_category = EndpointCategory.EMBEDDING
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
            acquire_deadline_mono=context._capacity_deadline_mono,
        )
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == ErrorCode.GATEWAY_AT_CAPACITY
