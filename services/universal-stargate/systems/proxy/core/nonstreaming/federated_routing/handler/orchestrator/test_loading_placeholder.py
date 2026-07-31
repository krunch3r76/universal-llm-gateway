import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    admission as admission_mod,
)


@dataclass(frozen=True)
class _FakeModelId:
    routing_key: str

    def __str__(self) -> str:
        return self.routing_key


@dataclass
class _FakeToken:
    gateway_id: str
    queued: bool = False


class _FakeCapacityPool:
    def __init__(self) -> None:
        self.set_calls: list[tuple[str, str, int]] = []
        self.acquire_calls: list[tuple[str, str, frozenset[str]]] = []

    def set_capacity(self, gateway_id: str, model_id: str, max_concurrent: int) -> None:
        self.set_calls.append((gateway_id, model_id, max_concurrent))

    async def acquire_token(
        self,
        request_id: str,
        model_id: str,
        allowed_gateway_ids: frozenset[str],
    ) -> _FakeToken:
        self.acquire_calls.append((request_id, model_id, allowed_gateway_ids))
        gateway_id = next(iter(allowed_gateway_ids))
        return _FakeToken(gateway_id=gateway_id)


class _FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


class _FakeStabilityTracker:
    def update_binding(self, model_id: Any, gateway_id: str) -> None:
        return None

    def clear_binding(self, model_id: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_cold_load_uses_loading_placeholder_and_emits_catalog_capacity() -> None:
    model_id = _FakeModelId("qwen3-5-9b-q8-0-262144")
    gateway = SimpleNamespace(
        name="edge-jupiter-gateway",
        loaded_models=frozenset(),
        loading_models=frozenset(),
        busy_models=frozenset(),
        model_details={model_id: {"max_concurrent_requests": 32}},
    )
    context = SimpleNamespace(
        request_id="req-1",
        selected_model=model_id,
        model_sticky=False,
        capacity_token=None,
    )
    pool = _FakeCapacityPool()
    event_bus = _FakeEventBus()

    selected = await admission_mod.acquire_admission_token(
        context=context,
        selected_gateway=gateway,
        gateways_for_routing=[gateway],
        routing_config={"capacity_pool": {"loading_phase_cap": 1}},
        event_bus=event_bus,
        capacity_pool=pool,
        stability_tracker=_FakeStabilityTracker(),
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
    )

    await asyncio.sleep(0)

    assert selected is gateway
    assert pool.set_calls == [("edge-jupiter-gateway", "qwen3-5-9b-q8-0-262144", 1)]
    assert pool.acquire_calls == [
        (
            "req-1",
            "qwen3-5-9b-q8-0-262144",
            frozenset({"edge-jupiter-gateway"}),
        )
    ]
    assert event_bus.events[0].signal == "routing.capacity.preseeded"
    assert event_bus.events[0].payload["placeholder_capacity"] == 1
    assert event_bus.events[0].payload["catalog_capacity"] == 32
