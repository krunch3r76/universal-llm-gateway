"""Regression: full idle eviction with understated catalog VRAM (replay 90489e45)."""

from __future__ import annotations

import pytest
from model_id import ModelId

from systems.routing import rag_protected_models
from systems.routing.selection.decision import eviction_planning, feasibility_reclaim

from systems.routing.selection.decision.admission_verdict import (
    AdmissionVerdict,
    evaluate_vram_reclaim,
)
from systems.routing.selection.decision.config import RoutingPolicy
from systems.routing.selection.decision.feasibility import evaluate_feasibility
from systems.routing.selection.decision.feasibility_reclaim import (
    can_fit_after_eviction_including_busy,
)
from systems.routing.selection.decision.types import FeasibilityTier
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
# 2% capped margin → 603MB; needed 30_787MB matches production scheduler event.


@pytest.fixture(autouse=True)
def _disable_rag_eviction_shield(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specimen had both qwens evictable; isolate from live rag yaml routing keys."""
    noop = lambda _m: False
    monkeypatch.setattr(rag_protected_models, "is_rag_protected_model", noop)
    monkeypatch.setattr(feasibility_reclaim, "is_rag_protected_model", noop)
    monkeypatch.setattr(eviction_planning, "is_rag_protected_model", noop)


def _requirements(
    table: dict[ModelId, tuple[int, int]],
):
    def lookup(model_id: ModelId) -> tuple[int, int]:
        return table.get(model_id, (0, 0))

    return lookup


def _specimen_gateway() -> Gateway:
  """edge-jupiter-gateway snapshot: hw 32607/1655 free, two idle qwens."""
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
          # Catalog totals far below hardware-used VRAM (~30_952MB).
          _QWEN_14B: {"vram_usage": 9_500, "ram_usage": 0},
          _QWEN_5B: {"vram_usage": 4_200, "ram_usage": 0},
      },
  )


def test_vram_reclaim_specimen_hardware_correction() -> None:
    used_mb = 32_607 - 1_655
    reserve_mb = max(512, int(32_607 * 0.02))
    catalog_sum = 9_500 + 4_200
    evaluation = evaluate_vram_reclaim(
        needed_mb=30_787,
        catalog_freeable_mb=catalog_sum,
        vram_free_mb=1_655,
        vram_total_mb=32_607,
        effective_free_mb=1_655,
        full_evict=True,
    )
    assert evaluation.correction_applied is True
    assert evaluation.freeable_mb == used_mb - reserve_mb
    assert 1_655 + evaluation.freeable_mb >= 30_787


def test_reclaim_classifier_fits_after_full_idle_evict() -> None:
    gw = _specimen_gateway()
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=_HERMES_FOOTPRINT_MB,
        is_gpu=True,
    )
    can_fit, diag = can_fit_after_eviction_including_busy(
        gw,
        placement,
        _requirements(
            {
                _HERMES: (_HERMES_FOOTPRINT_MB, 0),
                _QWEN_14B: (9_500, 0),
                _QWEN_5B: (4_200, 0),
            }
        ),
        _SPECIMEN_MARGINS,
    )
    assert can_fit is True
    assert diag["vram_correction_applied"] is True
    assert diag["max_freeable_vram"] >= diag["required_vram"]


def test_evaluate_feasibility_t2_evicts_both_idle_qwens() -> None:
    gw = _specimen_gateway()
    placement = Placement(
        model_id=_HERMES,
        ram_mb=0,
        vram_mb=_HERMES_FOOTPRINT_MB,
        is_gpu=True,
    )
    policy = RoutingPolicy(resource_margins=dict(_SPECIMEN_MARGINS))
    tier, failures, eviction_plan = evaluate_feasibility(
        gw,
        placement,
        policy,
        _requirements(
            {
                _HERMES: (_HERMES_FOOTPRINT_MB, 0),
                _QWEN_14B: (9_500, 0),
                _QWEN_5B: (4_200, 0),
            }
        ),
    )
    assert tier is FeasibilityTier.T2_FEASIBLE_EVICT
    assert failures == ()
    assert eviction_plan is not None
    assert eviction_plan.models_to_evict == frozenset({_QWEN_14B, _QWEN_5B})
    assert eviction_plan.hardware_correction_applied is True


def test_oversized_model_structural_not_retryable() -> None:
    gw = _specimen_gateway()
    huge = ModelId.parse("too-large-model")
    gw.model_details[huge] = {"vram_usage": 40_000, "ram_usage": 0}
    gw.available_models = frozenset({huge, _QWEN_14B, _QWEN_5B})
    placement = Placement(model_id=huge, ram_mb=0, vram_mb=40_000, is_gpu=True)
    can_fit, diag = can_fit_after_eviction_including_busy(
        gw,
        placement,
        _requirements({huge: (40_000, 0), _QWEN_14B: (9_500, 0), _QWEN_5B: (4_200, 0)}),
        _SPECIMEN_MARGINS,
    )
    assert can_fit is False
    assert diag["verdict_class"] == AdmissionVerdict.INSUFFICIENT_STRUCTURAL.value
