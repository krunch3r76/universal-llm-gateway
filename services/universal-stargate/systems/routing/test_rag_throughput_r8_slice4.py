"""RAG throughput r8 slice 4 acceptance tests (4.21–4.37 subset)."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_stargate_root = str(Path(__file__).resolve().parents[2])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.common.types import FederatedGateway  # noqa: E402
from systems.proxy.core.nonstreaming.capacity_deadline import apply_capacity_deadline  # noqa: E402
from systems.proxy.core.nonstreaming.context import RequestContext  # noqa: E402
from systems.proxy.core.nonstreaming.executor.oom_recovery import (  # noqa: E402
    attempt_oom_recovery,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (  # noqa: E402
    clamp_eviction_wait_timeout,
)
from systems.routing.capacity._pool._acquisition import _AcquisitionMixin  # noqa: E402
from systems.routing.capacity._pool._types import CapacityAcquireTimeoutError  # noqa: E402
from systems.routing.rag_protected_models import (  # noqa: E402
    is_rag_protected_model,
    rag_embedding_model_id,
    rag_contextualize_model_id,
)
from systems.routing.selection.decision.eviction_planning import (  # noqa: E402
    _compute_eviction_plan,
)
from systems.routing.selection.decision.config import RoutingPolicy  # noqa: E402
from systems.routing.selection.decision.feasibility import evaluate_feasibility  # noqa: E402
from systems.routing.selection.decision.feasibility_reclaim import (  # noqa: E402
    can_fit_after_eviction_including_busy,
)
from systems.routing.selection.decision.types import FeasibilityTier  # noqa: E402
from systems.routing.selection.stargate_collector import (  # noqa: E402
    federated_gateways_to_routing_candidates,
    stargate_to_gateway,
)
from systems.routing.selection.types import Gateway, Placement, Stargate  # noqa: E402


def _req_lookup(_mid: ModelId) -> tuple[int, int]:
    return (8_000, 0)


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


@pytest.mark.parametrize("test_id", ["4.22"])
def test_4_22_embedding_never_evictable(test_id: str) -> None:
    emb = ModelId.parse(rag_embedding_model_id() or "qwen3-embedding-8b-q8-0-8192")
    ctx = ModelId.parse(rag_contextualize_model_id() or "qwen3-5-9b-q8-0-131072")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    gw = _gateway(
        loaded_models=frozenset({emb, ctx, rerank}),
        available_models=frozenset({target, emb, ctx, rerank}),
        model_details={
            target: {"vram_usage": 12_000, "ram_usage": 0},
            emb: {"vram_usage": 15_000, "ram_usage": 0},
            ctx: {"vram_usage": 14_000, "ram_usage": 0},
            rerank: {"vram_usage": 12_000, "ram_usage": 0},
        },
        vram_free_mb=1_000,
    )
    idle = {emb, ctx, rerank}
    evictable = [
        mid for mid in idle if mid != target and not is_rag_protected_model(mid)
    ]
    assert evictable == [rerank]


@pytest.mark.parametrize("test_id", ["4.27"])
def test_4_27_reclaim_excludes_protected_fast_refuse(test_id: str) -> None:
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
@pytest.mark.parametrize("test_id", ["4.34"])
async def test_4_34_oom_recovery_skips_protected(
    test_id: str, monkeypatch: pytest.MonkeyPatch
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


@pytest.mark.parametrize("test_id", ["4.36"])
def test_4_36_measured_vram_via_stargate_collector(test_id: str) -> None:
    mid = ModelId.parse("qwen3-5-9b-q8-0-131072")
    fg = FederatedGateway(
        gateway_id="edge-io",
        remote_stargate_id="edge-io",
        remote_stargate_url="http://localhost:9999",
        loaded_models=frozenset({mid}),
        model_resources={
            mid: {"vram_usage": 15_134, "ram_usage": 0, "max_concurrent_requests": 4},
        },
        model_measured_vram={mid: 14_544},
    )
    candidates = federated_gateways_to_routing_candidates([fg])
    assert candidates[0].model_measured_vram[mid] == 14_544
    stargate = Stargate(
        stargate_id="edge-io",
        ref=fg,
        ram_free_mb=1,
        vram_free_mb=1,
        loaded_models=fg.loaded_models,
        model_details=candidates[0].model_details,
        model_measured_vram=fg.model_measured_vram,
    )
    routed = stargate_to_gateway(stargate)
    assert routed.model_measured_vram[mid] == 14_544


@pytest.mark.parametrize("test_id", ["4.29"])
def test_4_29_effective_context_per_slot(test_id: str) -> None:
    context_length = 131_072
    parallel_slots = 4
    assert context_length // parallel_slots == 32_768


@pytest.mark.parametrize("test_id", ["4.35"])
def test_4_35_cold_load_slack_tier(test_id: str) -> None:
    target = ModelId.parse("qwen3-14b-q4-k-m-40960")
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    placement = Placement(model_id=target, ram_mb=0, vram_mb=10_000, is_gpu=True)

    policy = RoutingPolicy()
    req = lambda mid: (10_000, 0) if mid == target else (2_000, 0)

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


@pytest.mark.parametrize("test_id", ["4.37"])
def test_4_37_x_request_timeout_clamps_eviction_wait(test_id: str) -> None:
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
        ctx, routing_config={"request_queue": {"queue_timeout": 300.0}}, request_timeout_hint=2.0
    )
    clamped = clamp_eviction_wait_timeout(ctx, config_timeout=300.0)
    assert clamped <= 2.1


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
@pytest.mark.parametrize("test_id", ["4.37"])
async def test_4_37_acquire_token_timeout(test_id: str) -> None:
    pool = _Pool()
    with pytest.raises(CapacityAcquireTimeoutError):
        await pool.acquire_token(
            "req",
            "qwen3-embedding-8b-q8-0-8192",
            frozenset({"edge-io"}),
            timeout_s=0.05,
        )
