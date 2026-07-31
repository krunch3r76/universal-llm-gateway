"""Tests for class-aware cooldown eviction planning."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.routing.selection.decision.eviction_cooldown_policy import (  # noqa: E402
    COOLDOWN_HARD_FLOOR_S,
    EvictionRequestClass,
)
from systems.routing.selection.decision.eviction_planning import (  # noqa: E402
    _compute_eviction_plan,
)
from systems.routing.selection.types import Gateway, Placement  # noqa: E402

GEMMA = ModelId.parse("gemma-3-27b-q4-0-8192")
TARGET = ModelId.parse("qwen3-32b-awq-16384")


def _requirements(model_id: ModelId) -> tuple[int, int]:
    table = {
        GEMMA: (12_000, 0),
        TARGET: (20_000, 0),
    }
    return table.get(model_id, (8_000, 0))


def _gateway(*, loaded_at: float) -> Gateway:
    now = time.monotonic()
    return Gateway(
        ref=None,
        name="edge-jupiter-gateway",
        node_id="jupiter",
        ram_free_mb=100_000,
        vram_free_mb=1_000,
        ram_total_mb=100_000,
        vram_total_mb=80_000,
        loaded_models=frozenset({GEMMA}),
        available_models=frozenset({GEMMA, TARGET}),
        model_details={
            GEMMA: {"vram_usage": 12_000, "ram_usage": 0},
            TARGET: {"vram_usage": 20_000, "ram_usage": 0},
        },
        model_loaded_at={GEMMA: now - (120.0 - loaded_at)},
    )


def _placement() -> Placement:
    return Placement(model_id=TARGET, ram_mb=0, vram_mb=20_000, is_gpu=True)


def test_required_cooldown_override_plans_gemma_with_101s_remaining() -> None:
    """REQUIRED class may override cooldown when remaining is above hard floor."""
    plan = _compute_eviction_plan(
        _gateway(loaded_at=101.0),
        _placement(),
        _requirements,
        eviction_cooldown_s=120.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )

    assert plan is not None
    assert GEMMA in plan.models_to_evict
    assert plan.cooldown_override_pending is True
    assert plan.cooldown_override_victim_id == str(GEMMA)
    assert plan.cooldown_override_remaining_s == pytest.approx(101.0, abs=0.5)


def test_opportunistic_blocks_when_all_candidates_in_cooldown() -> None:
    """OPPORTUNISTIC class must not override cooldown-protected candidates."""
    plan = _compute_eviction_plan(
        _gateway(loaded_at=101.0),
        _placement(),
        _requirements,
        eviction_cooldown_s=120.0,
        eviction_request_class=EvictionRequestClass.OPPORTUNISTIC,
    )

    assert plan is None


def test_required_blocks_at_or_below_hard_floor() -> None:
    """REQUIRED override is blocked when remaining cooldown is at/below hard floor."""
    plan = _compute_eviction_plan(
        _gateway(loaded_at=COOLDOWN_HARD_FLOOR_S),
        _placement(),
        _requirements,
        eviction_cooldown_s=120.0,
        eviction_request_class=EvictionRequestClass.REQUIRED,
    )

    assert plan is None
