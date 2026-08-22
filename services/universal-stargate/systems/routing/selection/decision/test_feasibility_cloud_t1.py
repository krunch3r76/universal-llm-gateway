"""Cloud catalog hit is T1 without treating catalog as GPU-resident."""

from __future__ import annotations

from model_id import ModelId

from systems.routing.selection.decision.config import RoutingPolicy
from systems.routing.selection.decision.feasibility import evaluate_feasibility
from systems.routing.selection.decision.feasibility_gates import early_feasibility_gates
from systems.routing.selection.decision.types import FeasibilityTier
from systems.routing.selection.types import Gateway, Placement


def _cloud_gateway(model: ModelId) -> Gateway:
    return Gateway(
        ref=None,
        name="cloud-anthropic",
        ram_free_mb=0,
        vram_free_mb=0,
        ram_total_mb=0,
        vram_total_mb=0,
        loaded_models=frozenset(),
        available_models=frozenset({model}),
        is_cloud=True,
    )


def test_early_gates_cloud_catalog_hit_is_t1() -> None:
    model = ModelId.parse("anthropic/claude-sonnet-5")
    placement = Placement(model_id=model, ram_mb=0, vram_mb=0, is_gpu=True)
    result = early_feasibility_gates(
        _cloud_gateway(model),
        placement,
        sticky=False,
        is_gateway_available_fn=None,
    )
    assert result is not None
    tier, failures, plan = result
    assert tier is FeasibilityTier.T1_FEASIBLE_NOW
    assert failures == ()
    assert plan is None


def test_early_gates_local_empty_loaded_does_not_short_circuit() -> None:
    model = ModelId.parse("qwen3-14b-q4-k-m-40960")
    gateway = Gateway(
        ref=None,
        name="edge-localhost-gateway",
        ram_free_mb=0,
        vram_free_mb=0,
        ram_total_mb=0,
        vram_total_mb=0,
        loaded_models=frozenset(),
        available_models=frozenset({model}),
        is_cloud=False,
    )
    placement = Placement(model_id=model, ram_mb=0, vram_mb=16000, is_gpu=True)
    result = early_feasibility_gates(
        gateway,
        placement,
        sticky=False,
        is_gateway_available_fn=None,
    )
    assert result is None


def test_evaluate_feasibility_cloud_t1_without_vram() -> None:
    model = ModelId.parse("anthropic/claude-sonnet-5")
    placement = Placement(model_id=model, ram_mb=0, vram_mb=0, is_gpu=True)
    tier, failures, plan = evaluate_feasibility(
        _cloud_gateway(model),
        placement,
        RoutingPolicy(),
        requirements_lookup=lambda _mid: (0, 0),
        sticky=False,
    )
    assert tier is FeasibilityTier.T1_FEASIBLE_NOW
    assert failures == ()
    assert plan is None
