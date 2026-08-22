"""Boot-union membership grace: wait when slug is in no catalog during startup."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

_stargate_root = str(Path(__file__).resolve().parents[7])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402
from universal_protocol import ErrorCode  # noqa: E402

from systems.federation.common.config.schema import EndpointCategory  # noqa: E402
from systems.proxy.core.nonstreaming.federated_routing.handler.orchestrator import (  # noqa: E402
    selection as selection_mod,
)

TARGET = ModelId.parse("qwen3-14b-q4-k-m-40960")
GEMMA = ModelId.parse("gemma-3-27b-q4-0-8192")


def _localhost(*, models: frozenset[ModelId]) -> SimpleNamespace:
    return SimpleNamespace(
        gateway_id="localhost",
        dispatchable=True,
        available_models=models,
        loaded_models=frozenset(),
        model_resources={},
        heartbeat_age_ms=0,
        telemetry_age_ms=0,
        is_unreachable=False,
    )


def _jupiter_with_target() -> SimpleNamespace:
    return SimpleNamespace(
        gateway_id="edge-jupiter-gateway",
        dispatchable=True,
        available_models=frozenset({TARGET}),
        loaded_models=frozenset(),
        model_resources={},
        heartbeat_age_ms=0,
        telemetry_age_ms=0,
        is_unreachable=False,
    )


class _Manager:
    def __init__(
        self,
        *,
        all_gateways: list[Any],
        healthy: list[Any],
        uptime_s: float,
    ) -> None:
        self._all = list(all_gateways)
        self._healthy = list(healthy)
        self.uptime_s = uptime_s

    def get_all_gateways(self) -> list[Any]:
        return list(self._all)

    def get_healthy_gateways(self) -> list[Any]:
        return list(self._healthy)

    def is_inference_banned(self, _gateway: str, _model: Any) -> bool:
        return False


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        request_id="req-boot-union",
        selected_model=TARGET,
        model_sticky=False,
        excluded_gateway_ids=set(),
        routing_endpoint_category=EndpointCategory.GENERATION,
        http_request=SimpleNamespace(),
    )


def _startup_config() -> dict[str, Any]:
    return {"request_queue": {"startup_queue_timeout_s": 180.0}}


async def _run(manager: _Manager, routing_config: dict[str, Any]) -> Any:
    return await selection_mod.run_initial_selection(
        context=_context(),
        federated_manager=manager,
        federated_load_orchestrator=None,
        event_bus=None,
        routing_config=routing_config,
        stability_tracker=SimpleNamespace(),
        routing_key_tracker=None,
        capacity_pool=None,
        circuit_breaker=None,
    )


@pytest.mark.asyncio
async def test_boot_union_grace_timeout_still_404s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    localhost = _localhost(models=frozenset({GEMMA}))
    manager = _Manager(all_gateways=[localhost], healthy=[localhost], uptime_s=1.0)
    wait_calls: list[dict[str, Any]] = []

    async def _wait(**kwargs: Any) -> bool:
        wait_calls.append(kwargs)
        return False

    monkeypatch.setattr(selection_mod, "wait_for_model_gateway", _wait)

    with pytest.raises(HTTPException) as caught:
        await _run(manager, _startup_config())

    assert caught.value.detail["code"] == ErrorCode.MODEL_NOT_FOUND
    assert len(wait_calls) == 1
    assert wait_calls[0]["timeout_s"] == 179.0
    assert wait_calls[0]["unhealthy_gateway_ids"] == []
    assert wait_calls[0]["model_id"] == str(TARGET)


@pytest.mark.asyncio
async def test_boot_union_grace_waits_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    localhost = _localhost(models=frozenset({GEMMA}))
    jupiter = _jupiter_with_target()
    manager = _Manager(all_gateways=[localhost], healthy=[localhost], uptime_s=1.0)
    wait_calls: list[dict[str, Any]] = []

    async def _wait(**kwargs: Any) -> bool:
        wait_calls.append(kwargs)
        manager._all = [localhost, jupiter]
        manager._healthy = [localhost, jupiter]
        return True

    monkeypatch.setattr(selection_mod, "wait_for_model_gateway", _wait)

    class _Engine:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        def select(self, **_kwargs: Any) -> tuple[Any, Any]:
            return SimpleNamespace(name="edge-jupiter-gateway"), SimpleNamespace()

    async def _no_overflow(**kwargs: Any) -> tuple[Any, Any, None, None, int]:
        return kwargs["selected_gateway"], kwargs["trace"], None, None, 0

    monkeypatch.setattr(
        "systems.routing.selection.decision.DecisionEngine",
        _Engine,
    )
    monkeypatch.setattr(
        "systems.routing.selection.decision.config.load_routing_policy",
        lambda _cfg: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "systems.routing.selection.stargate_collector.federated_gateways_to_routing_candidates",
        lambda _gws: [SimpleNamespace(name="edge-jupiter-gateway")],
    )
    monkeypatch.setattr(selection_mod, "apply_non_sticky_overflow", _no_overflow)

    result = await _run(manager, _startup_config())
    assert wait_calls, "startup remaining must wait for catalog membership"
    assert result[0].name == "edge-jupiter-gateway"


@pytest.mark.asyncio
async def test_boot_union_past_startup_does_not_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    localhost = _localhost(models=frozenset({GEMMA}))
    manager = _Manager(all_gateways=[localhost], healthy=[localhost], uptime_s=5000.0)
    wait_calls: list[dict[str, Any]] = []

    async def _wait(**kwargs: Any) -> bool:
        wait_calls.append(kwargs)
        return False

    monkeypatch.setattr(selection_mod, "wait_for_model_gateway", _wait)

    with pytest.raises(HTTPException) as caught:
        await _run(manager, _startup_config())

    assert caught.value.detail["code"] == ErrorCode.MODEL_NOT_FOUND
    assert wait_calls == []
