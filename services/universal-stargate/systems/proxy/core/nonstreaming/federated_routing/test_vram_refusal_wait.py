"""VRAM-refusal requeue uses gateway-scoped dwell, not depth-charged execution_failure."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from systems.proxy.core.nonstreaming.federated_routing.wait_logic import (
    VRAM_REFUSAL_INITIAL_DWELL_S,
    _wait_vram_refusal_and_retry_selection,
)


class _Gw:
    def __init__(self, gateway_id: str, ts: float) -> None:
        self.gateway_id = gateway_id
        self.dispatchable = True
        self.telemetry_timestamp = ts


class _Mgr:
    def __init__(self, gateways: list[_Gw]) -> None:
        self._gateways = {g.gateway_id: g for g in gateways}
        self._version = 0

    def get_all_gateways(self) -> list[_Gw]:
        return list(self._gateways.values())

    def get_state_version(self) -> int:
        return self._version

    async def wait_for_gateway_resource_update(
        self, gateway_id: str, since_timestamp: float, timeout_s: float
    ) -> bool:
        await asyncio.sleep(min(timeout_s, 0.01))
        gw = self._gateways.get(gateway_id)
        return gw is not None and gw.telemetry_timestamp > since_timestamp

    async def wait_for_state_change(self, _version: int, timeout_s: float) -> bool:
        await asyncio.sleep(min(timeout_s, 0.01))
        return False


@pytest.mark.asyncio
async def test_vram_refusal_wait_respects_shared_budget_and_dwell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )
    mgr = _Mgr([_Gw("edge-jupiter-gateway", ts=100.0)])
    select_calls = {"n": 0}

    def _select(**_kwargs):
        select_calls["n"] += 1
        return None, SimpleNamespace(candidates=())

    context = SimpleNamespace(
        request_id="req-a",
        model_sticky=True,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=100.0,
    )
    placement = SimpleNamespace(model_id="hermes-test")
    started = time.monotonic()
    _selected, _trace, waited_ms = await _wait_vram_refusal_and_retry_selection(
        federated_manager=mgr,
        decision_engine=SimpleNamespace(select=_select),
        placement=placement,
        context=context,
        event_bus=None,
        timeout_s=0.15,
        stability_tracker=SimpleNamespace(),
    )
    elapsed = time.monotonic() - started
    assert _selected is None
    assert select_calls["n"] >= 1
    assert waited_ms <= 200
    assert elapsed >= 0.05


@pytest.mark.asyncio
async def test_vram_refusal_resolved_event_includes_dwell_exit_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scheduling.events.routing import ROUTING_EVICTION_WAIT_RESOLVED

    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )
    mgr = _Mgr([_Gw("edge-jupiter-gateway", ts=100.0)])
    mgr._gateways["edge-jupiter-gateway"].telemetry_timestamp = 101.0

    def _select(**_kwargs):
        return SimpleNamespace(name="edge-jupiter-gateway"), SimpleNamespace(
            candidates=()
        )

    class _Bus:
        def __init__(self) -> None:
            self.events: list = []

        async def publish_nowait(self, event) -> None:
            self.events.append(event)

    bus = _Bus()
    context = SimpleNamespace(
        request_id="req-dwell",
        model_sticky=True,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=100.0,
    )
    placement = SimpleNamespace(model_id="hermes-test")

    selected, _trace, _waited = await _wait_vram_refusal_and_retry_selection(
        federated_manager=mgr,
        decision_engine=SimpleNamespace(select=_select),
        placement=placement,
        context=context,
        event_bus=bus,
        timeout_s=1.0,
        stability_tracker=SimpleNamespace(),
    )
    assert selected is not None
    resolved = [
        e
        for e in bus.events
        if getattr(e, "signal", None) == ROUTING_EVICTION_WAIT_RESOLVED
    ]
    assert len(resolved) == 1
    assert resolved[0].payload["wait_kind"] == "vram_refusal"
    assert resolved[0].payload["exit_reason"] == "resource_update"


@pytest.mark.asyncio
async def test_vram_refusal_two_requests_do_not_share_context_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )
    mgr = _Mgr([_Gw("edge-jupiter-gateway", ts=1.0)])
    ctx_a = SimpleNamespace(
        request_id="req-a",
        model_sticky=False,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=1.0,
    )
    ctx_b = SimpleNamespace(
        request_id="req-b",
        model_sticky=False,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=50.0,
    )
    placement = SimpleNamespace(model_id="m1")

    async def _run(ctx):
        return await _wait_vram_refusal_and_retry_selection(
            federated_manager=mgr,
            decision_engine=SimpleNamespace(
                select=lambda **_k: (None, SimpleNamespace(candidates=()))
            ),
            placement=placement,
            context=ctx,
            event_bus=None,
            timeout_s=0.05,
            stability_tracker=SimpleNamespace(),
        )

    await asyncio.gather(_run(ctx_a), _run(ctx_b))
    assert ctx_a._vram_refusal_telemetry_baseline == 1.0
    assert ctx_b._vram_refusal_telemetry_baseline == 50.0
