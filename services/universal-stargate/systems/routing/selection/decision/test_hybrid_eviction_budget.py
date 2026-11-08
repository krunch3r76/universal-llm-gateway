"""Hybrid variant footprint resolution and independent eviction wait budget."""

from __future__ import annotations

import time
from types import SimpleNamespace

from model_id import ModelId
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (
    clamp_eviction_wait_timeout,
)
from systems.routing.selection.decision.resource_checks import (
    resolve_gateway_requirements,
)

BASE = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768"
HYBRID = f"{BASE}-hybrid"


def test_exhausted_queue_deadline_still_yields_eviction_wait() -> None:
    context = SimpleNamespace(_capacity_deadline_mono=time.monotonic() - 1.0)
    assert clamp_eviction_wait_timeout(context, 300.0) > 0.0


def test_eviction_budget_shared_across_requeues() -> None:
    context = SimpleNamespace(_eviction_wait_deadline_mono=time.monotonic() - 1.0)
    assert clamp_eviction_wait_timeout(context, 300.0) == 0.0


def test_hybrid_placement_uses_hybrid_gpu_share() -> None:
    details = {
        "vram_usage": 44000,
        "ram_usage": 2000,
        "variants": {
            BASE: {"vram_usage": 44000, "ram_usage": 2000},
            HYBRID: {"vram_usage": 24000, "ram_usage": 22000},
        },
    }
    gateway = SimpleNamespace(name="gw", model_details={ModelId.parse(BASE): details})
    placement = SimpleNamespace(
        model_id=ModelId.parse(HYBRID), vram_mb=24000, ram_mb=22000
    )
    assert resolve_gateway_requirements(gateway, placement) == (24000, 22000)

    placement_base = SimpleNamespace(
        model_id=ModelId.parse(BASE), vram_mb=44000, ram_mb=2000
    )
    assert resolve_gateway_requirements(gateway, placement_base) == (44000, 2000)
