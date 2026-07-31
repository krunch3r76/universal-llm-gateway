import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from systems.federation.master.orchestration.config import (  # noqa: E402
    MODEL_LOAD_BACKSTOP_TIMEOUT_S,
    MODEL_LOAD_INNER_BUDGET_S,
    OrchestrationConfig,
)
from systems.federation.master.orchestration.load_orchestrator import (  # noqa: E402
    FederatedLoadOrchestrator,
)


@dataclass(frozen=True)
class _FakeModelId:
    routing_key: str

    def __str__(self) -> str:
        return self.routing_key


class _FastTimeoutForwarder:
    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        raise TimeoutError("edge admission timed out after 5.0s")


class _SlowForwarder:
    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"status": "ok"}


class _HangingForwarder:
    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        await asyncio.sleep(60)
        return {"status": "ok"}


class _FakeEvent:
    def __init__(self, signal: str, payload: dict[str, Any]) -> None:
        self.signal = signal
        self.payload = payload


class _FakeSubscription:
    def unsubscribe(self) -> bool:
        return True


class _FakeEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}

    def subscribe_async(self, signal: str, handler: Any, **_kwargs: Any) -> _FakeSubscription:
        self._handlers.setdefault(signal, []).append(handler)
        return _FakeSubscription()

    async def publish(self, event: _FakeEvent) -> None:
        for handler in self._handlers.get(event.signal, []):
            await handler(event)

    async def publish_nowait(self, event: Any) -> None:
        await self.publish(_FakeEvent(event.signal, dict(event.payload)))


def _gateway() -> SimpleNamespace:
    return SimpleNamespace(
        gateway_id="edge-jupiter-gateway",
        remote_stargate_id="edge-jupiter",
        remote_stargate_url="http://edge-jupiter",
        is_cloud=False,
    )


@pytest.mark.asyncio
async def test_inner_timeout_reports_actual_fast_failure() -> None:
    orchestrator = FederatedLoadOrchestrator(
        _FastTimeoutForwarder(),
        config=OrchestrationConfig(load_retry_count=0),
    )

    with pytest.raises(HTTPException) as excinfo:
        await orchestrator.ensure_model_loaded_on_remote(
            _gateway(),
            _FakeModelId("hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid"),
        )

    detail = excinfo.value.detail
    assert detail["code"] == "REQUEST_TIMEOUT"
    assert "Inner timeout loading" in detail["message"]
    assert "edge admission timed out" in detail["message"]
    assert "(budget 300s)" not in detail["message"]
    assert detail["data"]["timeout_kind"] == "load_inner"
    assert detail["data"]["exception_type"] == "TimeoutError"
    assert detail["data"]["timeout_budget_s"] == MODEL_LOAD_BACKSTOP_TIMEOUT_S
    assert detail["data"]["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_wall_clock_timeout_reports_elapsed_and_budget() -> None:
    with patch(
        "systems.federation.master.orchestration.config.MODEL_LOAD_INNER_BUDGET_S",
        0,
    ):
        orchestrator = FederatedLoadOrchestrator(
            _SlowForwarder(),
            config=OrchestrationConfig(
                load_timeout=0.01,
                coalesce_wait_timeout=30.01,
                load_retry_count=0,
            ),
        )

        with pytest.raises(HTTPException) as excinfo:
            await orchestrator.ensure_model_loaded_on_remote(
                _gateway(),
                _FakeModelId("hermes-3-llama-3-1-70b-uncensored-q4-k-m-16384-hybrid"),
            )

        detail = excinfo.value.detail
        assert detail["code"] == "LOAD_TIMEOUT"
        assert "Timeout loading" in detail["message"]
        assert "(budget 0.01s)" in detail["message"]
        assert detail["data"]["timeout_kind"] == "load_wall_clock"
        assert detail["data"]["timeout_budget_s"] == 0.01
        assert detail["data"]["elapsed_ms"] > 0


@pytest.mark.asyncio
async def test_terminal_load_failed_resolves_without_load_timeout() -> None:
    model = _FakeModelId("qwen3-32b-32768")
    event_bus = _FakeEventBus()
    orchestrator = FederatedLoadOrchestrator(
        _HangingForwarder(),
        config=OrchestrationConfig(
            load_retry_count=0,
            load_timeout=MODEL_LOAD_BACKSTOP_TIMEOUT_S,
        ),
        event_bus=event_bus,
    )

    async def emit_failure() -> None:
        await asyncio.sleep(0.01)
        from src.scheduling.events.model_lifecycle import MODEL_LOAD_FAILED

        await event_bus.publish(
            _FakeEvent(
                MODEL_LOAD_FAILED,
                {
                    "model_id": model.routing_key,
                    "error": "CUDA OOM during load",
                    "gateway_name": "edge-jupiter",
                },
            )
        )

    emit_task = asyncio.create_task(emit_failure())
    started = asyncio.get_running_loop().time()
    with pytest.raises(HTTPException) as excinfo:
        await orchestrator.ensure_model_loaded_on_remote(_gateway(), model)
    elapsed = asyncio.get_running_loop().time() - started
    await emit_task

    detail = excinfo.value.detail
    assert detail["code"] == "INSUFFICIENT_VRAM"
    assert detail["code"] != "LOAD_TIMEOUT"
    assert detail["source"] == "edge-jupiter-gateway"
    assert detail["retryable"] is True
    assert detail["data"]["reason"] == "CUDA OOM during load"
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_terminal_loaded_first_does_not_false_load_timeout() -> None:
    """LOADED wins while HTTP hangs — must not return false LOAD_TIMEOUT.

    Live falsify (deepseek 876949a2): model.loaded ~52s then LOAD_TIMEOUT at
    the same instant because LOADED-first fell through to (None, None).
    """
    model = _FakeModelId("deepseek-llm-67b-chat-q4-k-m-4096-hybrid")
    event_bus = _FakeEventBus()
    orchestrator = FederatedLoadOrchestrator(
        _HangingForwarder(),
        config=OrchestrationConfig(
            load_retry_count=0,
            load_timeout=MODEL_LOAD_BACKSTOP_TIMEOUT_S,
        ),
        event_bus=event_bus,
    )

    async def emit_loaded() -> None:
        await asyncio.sleep(0.01)
        from src.scheduling.events.model_lifecycle import MODEL_LOADED

        # Telemetry uses base id without -hybrid (live edge-localhost shape)
        await event_bus.publish(
            _FakeEvent(
                MODEL_LOADED,
                {
                    "model_id": "deepseek-llm-67b-chat-q4-k-m-4096",
                    "gateway_name": "edge-localhost-gateway",
                    "vram_mb": 31320,
                },
            )
        )

    emit_task = asyncio.create_task(emit_loaded())
    started = asyncio.get_running_loop().time()
    loaded = await orchestrator.ensure_model_loaded_on_remote(
        SimpleNamespace(
            gateway_id="edge-localhost-gateway",
            remote_stargate_id="edge-localhost",
            remote_stargate_url="http://localhost:9998",
            is_cloud=False,
        ),
        model,
    )
    elapsed = asyncio.get_running_loop().time() - started
    await emit_task

    assert loaded is True
    assert elapsed < 2.0


class _ProgressThenLoadedForwarder:
    def __init__(self, event_bus: _FakeEventBus, model: _FakeModelId) -> None:
        self._event_bus = event_bus
        self._model = model

    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        from src.scheduling.events.model_lifecycle import MODEL_LOADING_PROGRESS

        for pct in (10, 25, 50, 75, 90):
            await asyncio.sleep(0.01)
            await self._event_bus.publish(
                _FakeEvent(
                    MODEL_LOADING_PROGRESS,
                    {
                        "model_id": self._model.routing_key,
                        "phase": "weights",
                        "pct": pct,
                        "gateway_name": "edge-jupiter",
                        "url": "http://edge-jupiter",
                    },
                )
            )
        await asyncio.sleep(0.02)
        from src.scheduling.events.model_lifecycle import MODEL_LOADED

        await self._event_bus.publish(
            _FakeEvent(
                MODEL_LOADED,
                {
                    "model_id": self._model.routing_key,
                    "gateway_name": "edge-jupiter",
                },
            )
        )
        return {"status": "ok"}


class _ProgressThenSilentForwarder:
    def __init__(self, event_bus: _FakeEventBus, model: _FakeModelId) -> None:
        self._event_bus = event_bus
        self._model = model

    async def forward_model_load_request(self, **_: Any) -> dict[str, Any]:
        from src.scheduling.events.model_lifecycle import MODEL_LOADING_PROGRESS

        await self._event_bus.publish(
            _FakeEvent(
                MODEL_LOADING_PROGRESS,
                {
                    "model_id": self._model.routing_key,
                    "phase": "init",
                    "pct": 5,
                    "gateway_name": "edge-jupiter",
                    "url": "http://edge-jupiter",
                },
            )
        )
        await asyncio.Event().wait()
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_progress_heartbeats_prevent_idle_load_timeout() -> None:
    model = _FakeModelId("qwen3-32b-32768")
    event_bus = _FakeEventBus()
    orchestrator = FederatedLoadOrchestrator(
        _ProgressThenLoadedForwarder(event_bus, model),
        config=OrchestrationConfig(
            load_retry_count=0,
            load_timeout=MODEL_LOAD_BACKSTOP_TIMEOUT_S,
            load_idle_budget=1,
        ),
        event_bus=event_bus,
    )

    loaded = await orchestrator.ensure_model_loaded_on_remote(_gateway(), model)
    assert loaded is True


@pytest.mark.asyncio
async def test_progress_silence_triggers_idle_load_timeout_payload() -> None:
    model = _FakeModelId("qwen3-32b-32768")
    event_bus = _FakeEventBus()
    orchestrator = FederatedLoadOrchestrator(
        _ProgressThenSilentForwarder(event_bus, model),
        config=OrchestrationConfig(
            load_retry_count=0,
            load_timeout=MODEL_LOAD_BACKSTOP_TIMEOUT_S,
            load_idle_budget=0.05,
        ),
        event_bus=event_bus,
    )

    with pytest.raises(HTTPException) as excinfo:
        await orchestrator.ensure_model_loaded_on_remote(_gateway(), model)

    detail = excinfo.value.detail
    assert detail["code"] == "LOAD_TIMEOUT"
    assert detail["data"]["timeout_kind"] == "load_progress_idle"
    assert detail["data"]["idle_seconds"] >= 0.05
    assert detail["data"]["last_event"]["phase"] == "init"
    assert detail["data"]["last_event"]["pct"] == 5
    assert "Progress silence" in detail["message"]


def test_outer_backstop_strictly_exceeds_inner_budget() -> None:
    assert MODEL_LOAD_BACKSTOP_TIMEOUT_S > MODEL_LOAD_INNER_BUDGET_S
    config = OrchestrationConfig()
    assert config.load_timeout > MODEL_LOAD_INNER_BUDGET_S
    assert config.load_idle_budget < MODEL_LOAD_INNER_BUDGET_S
