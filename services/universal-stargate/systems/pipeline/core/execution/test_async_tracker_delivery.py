"""B3 regression tests — agent-bus result delivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from systems.pipeline.core.execution.async_tracker import (
    PipelineExecutionError,
    PipelineExecutionRecord,
    PipelineExecutionResult,
)
from systems.pipeline.core.execution.async_tracker_delivery import (
    _build_envelope,
    deliver_result,
)


@dataclass(slots=True)
class _FakeBus:
    events: list[Any] = field(default_factory=list)

    async def publish_nowait(self, event: Any) -> None:
        self.events.append(event)


def _make_record(**overrides: Any) -> PipelineExecutionRecord:
    base: dict[str, Any] = {
        "execution_id": "exec-1",
        "pipeline": "frontier-dispatch",
        "status": "completed",
        "started_at": "2026-04-19T00:00:00Z",
        "started_at_monotonic": 0.0,
        "completed_at": "2026-04-19T00:00:10Z",
        "completed_at_monotonic": 10.0,
        "result": PipelineExecutionResult(
            content="ok",
            model="openai/gpt-5.4",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            duration_s=10.0,
            reasoning=None,
        ),
        "error": None,
        "result_delivery": {
            "bus_thread": "626",
            "bus_from_agent": "orion",
            "bus_to_agent": "cursor",
            "bus_subject": "dispatch done",
        },
    }
    base.update(overrides)
    return PipelineExecutionRecord(**base)


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    monkeypatch.setattr(
        "systems.pipeline.core.execution.async_tracker_delivery.make_async_client",
        lambda *a, **k: httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ),
    )


@pytest.mark.asyncio
async def test_deliver_sent_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _FakeBus()
    record = _make_record()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(201, json={"id": 123, "turn_number": 7})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured["path"] == "/turns"
    assert captured["auth"] == "Bearer secret"
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.sent" in signals


@pytest.mark.asyncio
async def test_deliver_failed_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _FakeBus()
    record = _make_record()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid token"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    await deliver_result(record, event_bus=bus, auth_token="wrong")
    await asyncio.sleep(0)
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.failed" in signals


@pytest.mark.asyncio
async def test_deliver_skipped_on_incomplete_config() -> None:
    bus = _FakeBus()
    record = _make_record(result_delivery={"bus_thread": "626"})
    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.skipped" in signals


def test_envelope_contains_reasoning_and_error_fields() -> None:
    record = _make_record(
        status="failed",
        result=None,
        error=PipelineExecutionError(
            code="upstream_rejected",
            message="400",
            data={"error": {"type": "invalid_request_error"}},
        ),
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "orion",
            "bus_to_agent": "cursor",
        },
    )
    envelope = _build_envelope(record)
    assert "upstream_rejected" in envelope
    assert "invalid_request_error" in envelope
