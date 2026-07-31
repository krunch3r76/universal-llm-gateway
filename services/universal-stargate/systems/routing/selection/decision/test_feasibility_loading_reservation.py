"""
Regression tests for `_can_fit_after_eviction_including_busy` loading reservation.

Asserts the classifier mirrors `_check_resources` / `_compute_eviction_plan`
by subtracting VRAM/RAM reserved by `loading_models` (excluding target) before
summing reclaimable-by-eviction capacity from loaded models. Before this fix,
the classifier overstated reclaimable capacity whenever a parallel load was
in flight — producing transient classifications at rejection time that flipped
to permanent on the first wait-loop retry (observed as 100% waited_ms=0 bails
for knife-edge large models).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.routing.selection.decision.config import RoutingPolicy  # noqa: E402
from systems.routing.selection.decision.feasibility import (  # noqa: E402
    _can_fit_after_eviction_including_busy,
    evaluate_feasibility,
)
from systems.routing.selection.decision.types import FeasibilityTier  # noqa: E402
from systems.routing.selection.types import Gateway, Placement  # noqa: E402


def _make_gateway(
    *,
    vram_free_mb: int,
    ram_free_mb: int = 100_000,
    loaded_models: frozenset[ModelId] = frozenset(),
    loading_models: frozenset[ModelId] = frozenset(),
    available_models: frozenset[ModelId] = frozenset(),
    model_details: dict | None = None,
    model_measured_vram: dict | None = None,
) -> Gateway:
    return Gateway(
        ref=None,
        name="gw",
        ram_free_mb=ram_free_mb,
        vram_free_mb=vram_free_mb,
        ram_total_mb=100_000,
        vram_total_mb=80_000,
        loaded_models=loaded_models,
        loading_models=loading_models,
        available_models=available_models,
        model_details=model_details or {},
        model_measured_vram=model_measured_vram or {},
    )


@pytest.fixture
def target_model() -> ModelId:
    return ModelId.parse("target-model-q4")


@pytest.fixture
def other_model() -> ModelId:
    return ModelId.parse("other-model-q4")


@pytest.fixture
def loaded_model() -> ModelId:
    return ModelId.parse("loaded-model-q4")


def _placement(model_id: ModelId, vram_mb: int) -> Placement:
    return Placement(
        model_id=model_id,
        ram_mb=0,
        vram_mb=vram_mb,
        is_gpu=True,
    )


def _requirements(table: dict[ModelId, tuple[int, int]]):
    def lookup(model_id: ModelId) -> tuple[int, int]:
        return table.get(model_id, (0, 0))

    return lookup


def _with_target_resources(gw: Gateway, target: ModelId, vram_mb: int) -> Gateway:
    gw.model_details[target] = {"vram_usage": vram_mb, "ram_usage": 0}
    return gw


def test_loading_reservation_reduces_reclaimable(
    target_model: ModelId, other_model: ModelId
) -> None:
    """Case 1: loading_models={other}, no loaded, zero free VRAM.

    Before fix: classifier sees `vram_free_mb + sum(loaded) = 0` and returns
    False (correctly for this case), but the diagnostic field `vram_reserved_loading`
    was absent. After fix: diagnostic surfaces the reservation and the
    `effective_vram_free` driving the decision.
    """
    gw = _make_gateway(
        vram_free_mb=0,
        loading_models=frozenset({other_model}),
    )
    _with_target_resources(gw, target_model, 40_000)

    can_fit, diag = _can_fit_after_eviction_including_busy(
        gw,
        _placement(target_model, 40_000),
        _requirements({other_model: (20_000, 0)}),
    )

    assert can_fit is False
    assert diag["vram_reserved_loading"] == 20_000


def test_loading_reservation_catches_knife_edge_flip(
    target_model: ModelId, other_model: ModelId, loaded_model: ModelId
) -> None:
    """Reproduces the 14b/26b flip mechanism.

    Setup: target needs 40_000MB VRAM. One loaded model evictable for 25_000MB.
    Free VRAM 20_000MB. Reclaimable-ignoring-loading = 20_000 + 25_000 = 45_000
    (TRUE). A parallel load reserves 10_000MB → effective free = 10_000MB,
    reclaimable = 10_000 + 25_000 = 35_000 < 40_000 → FALSE.

    Before the fix this function returned TRUE in both cases, which produced
    the transient→permanent classifier flip on the first wait-loop retry.
    """
    gw = _make_gateway(
        vram_free_mb=20_000,
        loaded_models=frozenset({loaded_model}),
        loading_models=frozenset({other_model}),
        model_details={
            loaded_model: {"vram_usage": 25_000, "ram_usage": 0},
        },
    )
    _with_target_resources(gw, target_model, 40_000)

    can_fit, diag = _can_fit_after_eviction_including_busy(
        gw,
        _placement(target_model, 40_000),
        _requirements({loaded_model: (25_000, 0), other_model: (10_000, 0)}),
    )

    assert can_fit is False, (
        "loading reservation must cut reclaimable below required "
        f"(got reclaimable={diag.get('max_freeable_vram')})"
    )
    assert diag["vram_reserved_loading"] == 10_000


def test_target_in_loading_is_not_double_counted(target_model: ModelId) -> None:
    """Case 2: loading_models={target} → _compute_loading_reservation excludes target.

    Asserts `vram_reserved_loading == 0` when the only loading model is the
    target itself (matching `_check_resources`' other_loading - {target} semantics).
    """
    gw = _make_gateway(
        vram_free_mb=50_000,
        loading_models=frozenset({target_model}),
    )
    _with_target_resources(gw, target_model, 40_000)

    can_fit, diag = _can_fit_after_eviction_including_busy(
        gw,
        _placement(target_model, 40_000),
        _requirements({target_model: (40_000, 0)}),
    )

    assert diag["vram_reserved_loading"] == 0
    assert can_fit is True


def test_missing_loading_requirements_fail_conservative(
    target_model: ModelId, other_model: ModelId
) -> None:
    """When _compute_loading_reservation raises ValueError (loading model
    missing from catalog), classifier returns (False, {}) — conservative
    downgrade to permanent, matching _check_resources' behavior at that boundary.
    """
    gw = _make_gateway(
        vram_free_mb=100_000,
        loading_models=frozenset({other_model}),
    )
    _with_target_resources(gw, target_model, 40_000)

    can_fit, diag = _can_fit_after_eviction_including_busy(
        gw,
        _placement(target_model, 40_000),
        _requirements({}),
    )

    assert can_fit is False
    assert diag == {}


def test_reclaimable_without_loading(
    target_model: ModelId, loaded_model: ModelId
) -> None:
    """Case 3 sanity: no loading models, reclaimable = free + loaded.

    Asserts `vram_reserved_loading == 0` and the free+loaded arithmetic path
    is unchanged when `loading_models` is empty.
    """
    gw = _make_gateway(
        vram_free_mb=10_000,
        loaded_models=frozenset({loaded_model}),
        model_details={loaded_model: {"vram_usage": 35_000, "ram_usage": 0}},
    )
    _with_target_resources(gw, target_model, 40_000)

    can_fit, diag = _can_fit_after_eviction_including_busy(
        gw,
        _placement(target_model, 40_000),
        _requirements({loaded_model: (35_000, 0)}),
    )

    assert diag["vram_reserved_loading"] == 0
    assert diag["max_freeable_vram"] == 45_000
    assert can_fit is True


def test_low_slack_cold_load_prefers_eviction(
    target_model: ModelId, loaded_model: ModelId
) -> None:
    """Cold loads that barely fit should evict idle models before forwarding.

    This covers the embedding startup failure mode: routing saw enough free VRAM
    by catalog estimate, skipped eviction, then Gateway preflight rejected the
    actual load while an idle model was resident.
    """
    gw = _make_gateway(
        vram_free_mb=16_843,
        loaded_models=frozenset({loaded_model}),
        available_models=frozenset({target_model, loaded_model}),
        model_details={
            target_model: {"vram_usage": 14_000, "ram_usage": 0},
            loaded_model: {"vram_usage": 15_000, "ram_usage": 0},
        },
        model_measured_vram={loaded_model: 15_000},
    )

    tier, failures, eviction_plan = evaluate_feasibility(
        gw,
        _placement(target_model, 14_000),
        RoutingPolicy(),
        _requirements({target_model: (14_000, 0), loaded_model: (15_000, 0)}),
    )

    assert tier is FeasibilityTier.T2_FEASIBLE_EVICT
    assert failures == ()
    assert eviction_plan is not None
    assert eviction_plan.models_to_evict == frozenset({loaded_model})
