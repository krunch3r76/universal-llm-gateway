"""RAG protected models: evict-last ordering, not hard exclusion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from model_id import ModelId

import systems.routing.rag_protected_models as rag_protected_models
from systems.routing.selection.decision import (
    eviction_planning,
    eviction_victim_select,
)
from systems.routing.selection.decision import (
    scorer as scorer_mod,
)
from systems.routing.selection.decision.config import RoutingPolicy, ScoringWeights
from systems.routing.selection.decision.engine.core import DecisionEngine
from systems.routing.selection.decision.eviction_cooldown_policy import (
    EvictionRequestClass,
)
from systems.routing.selection.decision.eviction_planning import _compute_eviction_plan
from systems.routing.selection.decision.feasibility import evaluate_feasibility
from systems.routing.selection.decision.scorer import calculate_utility
from systems.routing.selection.decision.types import (
    EvictionPlanSummary,
    FeasibilityTier,
)
from systems.routing.selection.types import Gateway, Placement

_SPECIMEN_MARGINS = {
    "vram_margin_pct": 2,
    "vram_headroom_mb": 0,
    "vram_margin_cap_mb": 4096,
}

_HERMES = ModelId.parse(
    "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
)
_QWEN_14B = ModelId.parse("qwen3-14b-q4-k-m-40960")
_QWEN_5B = ModelId.parse("qwen3-5-9b-q8-0-131072")
_HERMES_FOOTPRINT_MB = 30_184


def _requirements(table: dict[ModelId, tuple[int, int]]):
    def lookup(model_id: ModelId) -> tuple[int, int]:
        return table.get(model_id, (0, 0))

    return lookup


def _specimen_gateway() -> Gateway:
    return Gateway(
        ref=None,
        name="edge-jupiter-gateway",
        vram_total_mb=32_607,
        vram_free_mb=1_655,
        ram_total_mb=64_000,
        ram_free_mb=32_000,
        loaded_models=frozenset({_QWEN_14B, _QWEN_5B}),
        busy_models=frozenset(),
        loading_models=frozenset(),
        available_models=frozenset({_HERMES, _QWEN_14B, _QWEN_5B}),
        model_details={
            _HERMES: {"vram_usage": _HERMES_FOOTPRINT_MB, "ram_usage": 0},
            _QWEN_14B: {"vram_usage": 9_500, "ram_usage": 0},
            _QWEN_5B: {"vram_usage": 4_200, "ram_usage": 0},
        },
    )


@pytest.fixture
def protect_qwen_5b(monkeypatch: pytest.MonkeyPatch) -> None:
    protected_key = _QWEN_5B.routing_key

    def _is_protected(mid: ModelId) -> bool:
        return mid.routing_key == protected_key

    monkeypatch.setattr(rag_protected_models, "is_rag_protected_model", _is_protected)
    monkeypatch.setattr(eviction_planning, "is_rag_protected_model", _is_protected)
    monkeypatch.setattr(eviction_victim_select, "is_rag_protected_model", _is_protected)
    monkeypatch.setattr(scorer_mod, "is_rag_protected_model", _is_protected)


def test_contextualize_does_not_evict_hermes_in_cooldown(protect_qwen_5b: None) -> None:
    """RAG contextualize must not evict freshly loaded Hermes (cooldown)."""
    import time

    hermes = _HERMES
    ctx = _QWEN_5B
    gw = Gateway(
        ref=None,
        name="edge-jupiter-gateway",
        vram_total_mb=32_607,
        vram_free_mb=2_000,
        ram_total_mb=64_000,
        ram_free_mb=32_000,
        loaded_models=frozenset({hermes}),
        busy_models=frozenset(),
        loading_models=frozenset(),
        available_models=frozenset({hermes, ctx}),
        model_details={
            hermes: {"vram_usage": _HERMES_FOOTPRINT_MB, "ram_usage": 0},
            ctx: {"vram_usage": 4_200, "ram_usage": 0},
        },
        model_loaded_at={hermes: time.monotonic()},
    )
    placement = Placement(
        model_id=ctx,
        ram_mb=0,
        vram_mb=4_200,
        is_gpu=True,
    )
    plan = _compute_eviction_plan(
        gw,
        placement,
        _requirements({hermes: (_HERMES_FOOTPRINT_MB, 0), ctx: (4_200, 0)}),
        eviction_cooldown_s=120.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    assert plan is None


def test_partial_eviction_first_victim_sufficient(protect_qwen_5b: None) -> None:
    """Three idle loaded models; evicting only non-protected rerank is enough (M1)."""
    rerank = ModelId.parse("baai-bge-reranker-v2-m3")
    target_vram = 12_000
    placement = Placement(
        model_id=_QWEN_14B,
        ram_mb=0,
        vram_mb=target_vram,
        is_gpu=True,
    )
    gw = Gateway(
        ref=None,
        name="edge-jupiter-gateway",
        vram_total_mb=32_607,
        vram_free_mb=12_500,
        ram_total_mb=64_000,
        ram_free_mb=32_000,
        loaded_models=frozenset({_QWEN_14B, _QWEN_5B, rerank}),
        busy_models=frozenset(),
        loading_models=frozenset(),
        available_models=frozenset({_QWEN_14B, _QWEN_5B, rerank}),
        model_details={
            _QWEN_14B: {"vram_usage": target_vram, "ram_usage": 0},
            _QWEN_5B: {"vram_usage": 4_200, "ram_usage": 0},
            rerank: {"vram_usage": 2_000, "ram_usage": 0},
        },
    )
    plan = _compute_eviction_plan(
        gw,
        placement,
        _requirements(
            {
                _QWEN_14B: (target_vram, 0),
                _QWEN_5B: (4_200, 0),
                rerank: (2_000, 0),
            }
        ),
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    assert isinstance(plan, EvictionPlanSummary)
    assert rerank in plan.models_to_evict
    assert _QWEN_5B not in plan.models_to_evict
    assert _QWEN_14B not in plan.models_to_evict


def test_jupiter_evicts_protected_qwen_when_required(protect_qwen_5b: None) -> None:
    gw = _specimen_gateway()
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=_HERMES_FOOTPRINT_MB,
        is_gpu=True,
    )
    tier, failures, plan = evaluate_feasibility(
        gw,
        placement,
        RoutingPolicy(resource_margins=dict(_SPECIMEN_MARGINS)),
        _requirements(
            {
                _HERMES: (_HERMES_FOOTPRINT_MB, 0),
                _QWEN_14B: (9_500, 0),
                _QWEN_5B: (4_200, 0),
            }
        ),
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    assert tier is FeasibilityTier.T2_FEASIBLE_EVICT
    assert failures == ()
    assert plan is not None
    assert _QWEN_5B in plan.models_to_evict
    assert _QWEN_14B in plan.models_to_evict


def test_busy_protected_model_not_evicted(protect_qwen_5b: None) -> None:
    gw = _specimen_gateway()
    gw.busy_models = frozenset({_QWEN_5B})
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=14_000,
        is_gpu=True,
    )
    gw.model_details[_HERMES] = {"vram_usage": 14_000, "ram_usage": 0}
    plan = _compute_eviction_plan(
        gw,
        placement,
        _requirements(
            {
                _HERMES: (14_000, 0),
                _QWEN_14B: (9_500, 0),
                _QWEN_5B: (4_200, 0),
            }
        ),
        eviction_cooldown_s=0.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )
    victims = (
        plan.models_to_evict if isinstance(plan, EvictionPlanSummary) else frozenset()
    )
    assert _QWEN_5B not in victims


def test_alternative_gateway_wins_without_protected_eviction(
    protect_qwen_5b: None,
) -> None:
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=_HERMES_FOOTPRINT_MB,
        is_gpu=True,
    )
    crowded = _specimen_gateway()
    roomy = Gateway(
        ref=SimpleNamespace(gateway_id="edge-saturn-gateway"),
        name="edge-saturn-gateway",
        vram_total_mb=80_000,
        vram_free_mb=50_000,
        ram_total_mb=64_000,
        ram_free_mb=32_000,
        loaded_models=frozenset(),
        busy_models=frozenset(),
        loading_models=frozenset(),
        available_models=frozenset({_HERMES}),
        model_details={_HERMES: {"vram_usage": _HERMES_FOOTPRINT_MB, "ram_usage": 0}},
    )

    policy = RoutingPolicy(resource_margins=dict(_SPECIMEN_MARGINS))
    engine = DecisionEngine(policy)
    selected, trace = engine.select([crowded, roomy], placement)

    assert selected is not None
    assert selected.name == "edge-saturn-gateway"
    assert trace.selection_tier == FeasibilityTier.T1_FEASIBLE_NOW


def test_scoring_penalizes_protected_eviction_plan(protect_qwen_5b: None) -> None:
    policy = RoutingPolicy(
        weights=ScoringWeights(rag_protected_eviction_extra=-100.0),
        resource_margins=dict(_SPECIMEN_MARGINS),
    )
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=_HERMES_FOOTPRINT_MB,
        is_gpu=True,
    )
    gw = _specimen_gateway()
    plan_non_protected = EvictionPlanSummary(
        models_to_evict=frozenset({_QWEN_14B}),
        freed_vram_mb=9_500,
        freed_ram_mb=0,
        estimated_cost=-50.0,
    )
    plan_with_protected = EvictionPlanSummary(
        models_to_evict=frozenset({_QWEN_14B, _QWEN_5B}),
        freed_vram_mb=13_700,
        freed_ram_mb=0,
        estimated_cost=-70.0,
    )
    _, total_clean = calculate_utility(
        gw,
        placement,
        policy,
        FeasibilityTier.T2_FEASIBLE_EVICT,
        plan_non_protected,
        None,
        sticky=True,
        current_best=None,
    )
    _, total_protected = calculate_utility(
        gw,
        placement,
        policy,
        FeasibilityTier.T2_FEASIBLE_EVICT,
        plan_with_protected,
        None,
        sticky=True,
        current_best=None,
    )
    assert total_clean > total_protected
