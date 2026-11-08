"""Federation load HTTP vs terminal race observation events."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_stargate_root = str(Path(__file__).resolve().parents[4])
if _stargate_root not in sys.path:
    sys.path.insert(0, _stargate_root)

from model_id import ModelId  # noqa: E402

from systems.federation.master.orchestration.load_terminal_wait import (  # noqa: E402
    TerminalLoadOutcome,
    race_remote_load_with_terminal_events,
)


class _FakeEvent:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeSub:
    def unsubscribe(self) -> bool:
        return True


class _RaceBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.published: list = []

    def subscribe_async(self, signal: str, handler, **_kwargs) -> _FakeSub:
        self._handlers.setdefault(signal, []).append(handler)
        return _FakeSub()

    async def publish(self, signal: str, payload: dict) -> None:
        for handler in self._handlers.get(signal, []):
            await handler(_FakeEvent(payload))

    async def publish_nowait(self, event) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_federation_load_race_observation_http_first() -> None:
    bus = _RaceBus()
    model = ModelId.parse("qwen3-14b-q4-k-m-40960")
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def http_ok() -> dict[str, str]:
        await asyncio.sleep(0.05)
        return {"status": "ok"}

    remote = asyncio.create_task(http_ok())
    _result, terminal = await race_remote_load_with_terminal_events(
        bus,
        remote,
        expected_model=model,
        gateway_id="edge-jupiter-gateway",
        remote_id="relay-jupiter",
        backstop_timeout_s=1.0,
        request_id="req-http-first",
        load_started_mono=t0,
    )
    race = [e for e in bus.published if e.signal == "federation.load.race_resolved"]
    assert len(race) == 1
    payload = race[0].payload
    assert payload["winner"] == "http_first"
    assert payload["request_id"] == "req-http-first"
    assert payload["model_id"] == str(model)
    assert payload["gateway_id"] == "edge-jupiter-gateway"
    assert payload["terminal_pending"] is True
    assert payload["http_arrival_ms"] is not None
    assert terminal is None


@pytest.mark.asyncio
async def test_federation_load_race_observation_terminal_first() -> None:
    from src.scheduling.events.model_lifecycle import MODEL_LOAD_FAILED

    bus = _RaceBus()
    model = ModelId.parse(
        "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
    )
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def slow_http() -> dict[str, str]:
        await asyncio.sleep(5.0)
        return {"status": "ok"}

    remote = asyncio.create_task(slow_http())

    async def emit_terminal() -> None:
        await asyncio.sleep(0.02)
        await bus.publish(
            MODEL_LOAD_FAILED,
            {
                "model_id": str(model),
                "error": "VRAM",
                "gateway_name": "edge-jupiter-gateway",
                "url": "http://relay-jupiter:9998/",
            },
        )

    asyncio.create_task(emit_terminal())
    result, terminal = await race_remote_load_with_terminal_events(
        bus,
        remote,
        expected_model=model,
        gateway_id="edge-jupiter-gateway",
        remote_id="relay-jupiter",
        backstop_timeout_s=2.0,
        request_id="req-terminal-first",
        load_started_mono=t0,
    )
    assert result is None
    assert terminal is not None
    assert terminal.outcome == TerminalLoadOutcome.FAILED
    race = [e for e in bus.published if e.signal == "federation.load.race_resolved"]
    assert len(race) == 1
    payload = race[0].payload
    assert payload["winner"] == "terminal_first"
    assert payload["terminal_outcome"] == "failed"
    assert payload["terminal_pending"] is False
    assert payload["terminal_arrival_ms"] is not None
    assert payload.get("http_arrival_ms") is None


@pytest.mark.asyncio
async def test_federation_load_race_observation_http_error_first() -> None:
    bus = _RaceBus()
    model = ModelId.parse("qwen3-14b-q4-k-m-40960")
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    async def http_fail() -> None:
        await asyncio.sleep(0.02)
        raise RuntimeError("edge refused")

    async def slow_terminal_publish() -> None:
        await asyncio.sleep(0.2)

    remote = asyncio.create_task(http_fail())
    asyncio.create_task(slow_terminal_publish())
    with pytest.raises(RuntimeError, match="edge refused"):
        await race_remote_load_with_terminal_events(
            bus,
            remote,
            expected_model=model,
            gateway_id="edge-jupiter-gateway",
            remote_id="relay-jupiter",
            backstop_timeout_s=1.0,
            request_id="req-http-err",
            load_started_mono=t0,
        )
    race = [e for e in bus.published if e.signal == "federation.load.race_resolved"]
    assert len(race) == 1
    payload = race[0].payload
    assert payload["winner"] == "http_error_first"
    assert payload["http_exception_class"] == "RuntimeError"
    assert payload["http_arrival_ms"] is not None
    assert payload["http_arrival_ms"] < 100
