"""VRAM-refusal requeue: gateway-scoped dwell, not depth-charged execution_failure."""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

import pytest

from systems.proxy.core.nonstreaming.federated_routing import (
    wait_logic as _wait_logic_mod,
)
from systems.proxy.core.nonstreaming.federated_routing.wait_logic import (
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

    def gateway_resource_telemetry_timestamp(self, gateway_id: str) -> float:
        gw = self._gateways.get(gateway_id)
        return gw.telemetry_timestamp if gw else 0.0


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
    assert waited_ms <= 400
    assert elapsed >= 0.05


@pytest.mark.asyncio
async def test_vram_refusal_resolved_event_includes_dwell_exit_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scheduling.events.routing_signal_constants_routing_waits import (
        ROUTING_EVICTION_VRAM_REFUSAL_WAIT_RESOLVED,
    )

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
        if getattr(e, "signal", None) == ROUTING_EVICTION_VRAM_REFUSAL_WAIT_RESOLVED
    ]
    assert len(resolved) == 1
    assert resolved[0].payload["exit_reason"] == "resource_update"


@pytest.mark.asyncio
async def test_vram_refusal_instant_resource_wake_still_awaits_dwell_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instant wait_for_gateway_resource_update must not spin without dwell pad (Z1)."""
    monkeypatch.setattr(_wait_logic_mod, "VRAM_REFUSAL_INITIAL_DWELL_S", 0.05)
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )

    class _InstantMgr(_Mgr):
        async def wait_for_gateway_resource_update(
            self, gateway_id: str, since_timestamp: float, timeout_s: float
        ) -> bool:
            _ = (gateway_id, since_timestamp, timeout_s)
            return True

        def gateway_resource_telemetry_timestamp(self, gateway_id: str) -> float:
            return self._gateways[gateway_id].telemetry_timestamp

    mgr = _InstantMgr([_Gw("edge-jupiter-gateway", ts=100.0)])
    ts = {"v": 100.0}
    mgr.gateway_resource_telemetry_timestamp = (  # type: ignore[method-assign]
        lambda gateway_id: ts["v"]
    )

    select_calls = {"n": 0}

    def _select(**_kwargs):
        select_calls["n"] += 1
        ts["v"] += 1.0
        return None, SimpleNamespace(candidates=())

    ticker_s = {"elapsed": 0.0}

    async def _ticker() -> None:
        while True:
            await asyncio.sleep(0.01)
            ticker_s["elapsed"] += 0.01

    tick = asyncio.create_task(_ticker())
    context = SimpleNamespace(
        request_id="req-z1",
        model_sticky=True,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=100.0,
    )
    placement = SimpleNamespace(model_id="hermes-test")
    timeout_s = 0.55
    try:
        await _wait_vram_refusal_and_retry_selection(
            federated_manager=mgr,
            decision_engine=SimpleNamespace(select=_select),
            placement=placement,
            context=context,
            event_bus=None,
            timeout_s=timeout_s,
            stability_tracker=SimpleNamespace(),
        )
    finally:
        tick.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick

    pad_floor = _wait_logic_mod.VRAM_REFUSAL_DWELL_PAD_FLOOR_S
    dwell_s = _wait_logic_mod.VRAM_REFUSAL_INITIAL_DWELL_S
    iter_min_s = min(dwell_s, pad_floor)
    max_select = int(timeout_s / iter_min_s) + 1
    assert select_calls["n"] >= 2
    assert select_calls["n"] <= max_select, (
        f"select spam bound: timeout/min(dwell,pad_floor)+1 = "
        f"{timeout_s}/{iter_min_s}+1 => {max_select} (pad_floor={pad_floor})"
    )
    assert ticker_s["elapsed"] >= 0.06


@pytest.mark.asyncio
async def test_vram_refusal_fast_resource_wake_selects_before_half_dwell_slice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESOURCE_UPDATE after ~10ms must not block first select until full dwell (R1)."""
    dwell_slice_s = 2.0
    monkeypatch.setattr(_wait_logic_mod, "VRAM_REFUSAL_INITIAL_DWELL_S", dwell_slice_s)
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )

    class _FastWakeMgr(_Mgr):
        def __init__(self, gateways: list[_Gw]) -> None:
            super().__init__(gateways)
            self._ts = gateways[0].telemetry_timestamp + 1.0

        async def wait_for_gateway_resource_update(
            self, gateway_id: str, since_timestamp: float, timeout_s: float
        ) -> bool:
            _ = (gateway_id, since_timestamp, timeout_s)
            await asyncio.sleep(0.01)
            self._gateways["edge-jupiter-gateway"].telemetry_timestamp = self._ts
            return True

        def gateway_resource_telemetry_timestamp(self, gateway_id: str) -> float:
            return self._gateways[gateway_id].telemetry_timestamp

    mgr = _FastWakeMgr([_Gw("edge-jupiter-gateway", ts=100.0)])
    select_at: list[float] = []

    def _select(**_kwargs):
        select_at.append(time.monotonic() - loop_start)
        return SimpleNamespace(name="edge-jupiter-gateway"), SimpleNamespace(
            candidates=()
        )

    context = SimpleNamespace(
        request_id="req-r1-fast",
        model_sticky=True,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=100.0,
    )
    placement = SimpleNamespace(model_id="hermes-test")
    loop_start = time.monotonic()
    await _wait_vram_refusal_and_retry_selection(
        federated_manager=mgr,
        decision_engine=SimpleNamespace(select=_select),
        placement=placement,
        context=context,
        event_bus=None,
        timeout_s=5.0,
        stability_tracker=SimpleNamespace(),
    )
    assert len(select_at) == 1
    assert select_at[0] < 0.5 * dwell_slice_s


@pytest.mark.asyncio
async def test_vram_refusal_cancel_emits_wait_cancelled_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.scheduling.events.routing_signal_constants_routing_waits import (
        ROUTING_EVICTION_WAIT_CANCELLED,
    )

    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda gateways: [
            SimpleNamespace(name=g.gateway_id) for g in gateways
        ],
    )
    mgr = _Mgr([_Gw("edge-jupiter-gateway", ts=1.0)])

    class _Bus:
        def __init__(self) -> None:
            self.events: list = []

        async def publish_nowait(self, event) -> None:
            self.events.append(event)

    bus = _Bus()
    context = SimpleNamespace(
        request_id="req-cancel",
        model_sticky=True,
        excluded_gateway_ids=set(),
        _vram_refusal_gateway_id="edge-jupiter-gateway",
        _vram_refusal_telemetry_baseline=1.0,
    )
    placement = SimpleNamespace(model_id="m1")

    wait_task = asyncio.create_task(
        _wait_vram_refusal_and_retry_selection(
            federated_manager=mgr,
            decision_engine=SimpleNamespace(
                select=lambda **_k: (None, SimpleNamespace(candidates=()))
            ),
            placement=placement,
            context=context,
            event_bus=bus,
            timeout_s=30.0,
            stability_tracker=SimpleNamespace(),
        )
    )
    await asyncio.sleep(0.05)
    wait_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wait_task
    cancelled = [
        e
        for e in bus.events
        if getattr(e, "signal", None) == ROUTING_EVICTION_WAIT_CANCELLED
    ]
    assert len(cancelled) == 1


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
