"""Hybrid variant footprint resolution and independent eviction wait budget."""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest
from model_id import ModelId

from systems.federation.common.config.schema import EndpointCategory
from systems.federation.common.types import parse_telemetry_payload
from systems.proxy.core.nonstreaming.capacity_deadline import apply_capacity_deadline
from systems.proxy.core.nonstreaming.federated_routing import (
    wait_continuation as wait_continuation_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_continuation import (
    arm_or_remaining_eviction_budget,
    get_eviction_wait_budget_s,
)
from systems.routing.selection.decision.resource_checks import (
    resolve_gateway_requirements,
)
from systems.routing.selection.stargate_collector import (
    federated_gateways_to_routing_candidates,
)

BASE = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768"
HYBRID = f"{BASE}-hybrid"


def _context(**kwargs: float | None | object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "_capacity_deadline_mono": None,
        "_eviction_wait_deadline_mono": None,
        "routing_endpoint_category": None,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


def test_exhausted_queue_deadline_still_yields_eviction_wait() -> None:
    now = time.monotonic()
    context = _context(_capacity_deadline_mono=now - 1.0)
    assert context._eviction_wait_deadline_mono is None
    assert arm_or_remaining_eviction_budget(context, 300.0) == pytest.approx(300.0)


def test_eviction_deadline_arms_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = {"t": 1000.0}

    def fake_mono() -> float:
        return ticks["t"]

    monkeypatch.setattr(wait_continuation_mod.time, "monotonic", fake_mono)
    context = _context()
    first = arm_or_remaining_eviction_budget(context, 300.0)
    assert first == pytest.approx(300.0)
    armed = context._eviction_wait_deadline_mono
    ticks["t"] = 1100.0
    second = arm_or_remaining_eviction_budget(context, 300.0)
    assert context._eviction_wait_deadline_mono == armed
    assert second == pytest.approx(200.0)


def test_eviction_budget_shared_across_requeues() -> None:
    context = _context(_eviction_wait_deadline_mono=time.monotonic() - 1.0)
    assert arm_or_remaining_eviction_budget(context, 300.0) == 0.0


def test_embedding_eviction_budget_capped_by_capacity_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mono = {"t": 1000.0}

    def fake_mono() -> float:
        return mono["t"]

    monkeypatch.setattr(wait_continuation_mod.time, "monotonic", fake_mono)
    monkeypatch.setattr(
        "systems.proxy.core.nonstreaming.capacity_deadline.time.monotonic",
        fake_mono,
    )
    context = _context(routing_endpoint_category=EndpointCategory.EMBEDDING)
    apply_capacity_deadline(
        context,
        routing_config={"request_queue": {"queue_timeout": 1800.0}},
        request_timeout_hint=30.0,
    )
    assert context._eviction_wait_deadline_mono is None
    first = arm_or_remaining_eviction_budget(context, 300.0)
    assert first == pytest.approx(30.0)
    armed = context._eviction_wait_deadline_mono
    assert armed == pytest.approx(1030.0)
    mono["t"] += 10.0
    second = arm_or_remaining_eviction_budget(context, 300.0)
    assert second == pytest.approx(20.0)
    assert second != pytest.approx(300.0)
    mono["t"] += 10.0
    third = arm_or_remaining_eviction_budget(context, 300.0)
    assert third == pytest.approx(10.0)
    assert context._eviction_wait_deadline_mono == armed


def test_nested_routing_config_eviction_budget() -> None:
    assert (
        get_eviction_wait_budget_s(
            {"routing": {"eviction_wait_timeout_s": 42.0}},
        )
        == 42.0
    )


def _federated_gateway(model_resources: dict) -> SimpleNamespace:
    parsed = parse_telemetry_payload("telemetry", {"model_resources": model_resources})
    return SimpleNamespace(
        gateway_id="gw-a",
        vram_total_mb=64000,
        vram_free_mb=30000,
        ram_total_mb=128000,
        ram_free_mb=100000,
        loaded_models=frozenset(),
        busy_models=frozenset(),
        loading_models=frozenset(),
        available_models=frozenset(parsed["model_resources"]),
        active_requests=0,
        telemetry_timestamp=time.time(),
        last_heartbeat=time.time(),
        model_resources=parsed["model_resources"],
        remote_stargate_id="sg-a",
        is_cloud=False,
    )


def _placement(model_id: str) -> SimpleNamespace:
    return SimpleNamespace(model_id=ModelId.parse(model_id), vram_mb=0, ram_mb=0)


def test_federated_hybrid_footprint_survives_to_routing() -> None:
    fg = _federated_gateway(
        {
            BASE: {"vram_usage": 44000, "ram_usage": 2000},
            HYBRID: {"vram_usage": 24000, "ram_usage": 22000},
        }
    )
    (candidate,) = federated_gateways_to_routing_candidates([fg])
    assert resolve_gateway_requirements(candidate, _placement(HYBRID)) == (
        24000,
        22000,
    )
    assert resolve_gateway_requirements(candidate, _placement(BASE)) == (44000, 2000)


def test_hybrid_first_order_does_not_change_resolution() -> None:
    fg = _federated_gateway(
        {
            HYBRID: {"vram_usage": 24000, "ram_usage": 22000},
            BASE: {"vram_usage": 44000, "ram_usage": 2000},
        }
    )
    (candidate,) = federated_gateways_to_routing_candidates([fg])
    assert resolve_gateway_requirements(candidate, _placement(HYBRID)) == (
        24000,
        22000,
    )
    assert resolve_gateway_requirements(candidate, _placement(BASE)) == (44000, 2000)


def test_warns_when_multiple_variants_and_none_match(caplog) -> None:
    other = ModelId.parse(f"{BASE}-cpu")
    gateway = SimpleNamespace(
        name="gw-a",
        model_details={
            ModelId.parse(BASE): {
                "vram_usage": 44000,
                "ram_usage": 2000,
                "variants": [
                    (other, {"vram_usage": 0, "ram_usage": 46000}),
                    (other, {"vram_usage": 0, "ram_usage": 46000}),
                ],
            }
        },
    )
    with caplog.at_level(logging.WARNING):
        assert resolve_gateway_requirements(gateway, _placement(BASE)) == (
            44000,
            2000,
        )
    assert "none match" in caplog.text
