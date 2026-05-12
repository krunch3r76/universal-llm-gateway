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
            "bus_from_agent": "gatherer",
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


def test_envelope_is_brief_pointer() -> None:
    """Brief envelope: pointer + error code/message; no content or error.data."""
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
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
        },
    )
    envelope = _build_envelope(record)
    assert "upstream_rejected" in envelope
    assert "400" in envelope
    # error.data is intentionally omitted to keep bodies under the 8 000-char limit
    assert "invalid_request_error" not in envelope
    # pointer line present so consumers know where to fetch the full result
    assert "poll" in envelope
    assert "exec-1" in envelope


def test_envelope_brief_summary_included() -> None:
    """bus_brief_summary appears in the envelope when provided."""
    import json

    record = _make_record()
    envelope = _build_envelope(record, brief_summary="Code review finished.")
    assert "Code review finished." in envelope
    # full model output must not be inlined (content key absent)
    parsed = json.loads(envelope)
    assert "content" not in parsed


# ---------------------------------------------------------------------------
# Ephemeral lifecycle — thread close after delivery
# ---------------------------------------------------------------------------


def _make_close_handler(
    captured: dict[str, object],
    status: int = 200,
) -> httpx.MockTransport:
    """Build a mock transport that handles /turns (201) and /threads/*/close."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/turns":
            return httpx.Response(201, json={"id": 1, "turn_number": 1})
        if "/close" in request.url.path:
            captured["close_called"] = True
            captured["close_path"] = request.url.path
            import json as _json

            captured["close_body"] = _json.loads(request.content)
            return httpx.Response(status, json={})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_ephemeral_closes_thread_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """ephemeral + successful delivery → close called; thread.closed event emitted."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record(
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
            "bus_subject": "done",
            "bus_lifecycle": "ephemeral",
        }
    )

    _patch_client(monkeypatch, _make_close_handler(captured))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is True
    assert captured["close_path"] == "/threads/626/close"
    close_body = captured["close_body"]
    assert isinstance(close_body, dict)
    assert close_body.get("mark_all_read") is True
    assert "completed" in close_body.get("summary", "")

    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.sent" in signals
    assert "mcp.agentbus.thread.closed" in signals


@pytest.mark.asyncio
async def test_ephemeral_skips_close_on_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ephemeral + failed delivery POST → close is never attempted."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record(
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
            "bus_lifecycle": "ephemeral",
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if "/close" in request.url.path:
            captured["close_called"] = True
        return httpx.Response(401, json={"detail": "Invalid token"})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    await deliver_result(record, event_bus=bus, auth_token="bad")
    await asyncio.sleep(0)

    assert captured.get("close_called") is None
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.failed" in signals
    assert "mcp.agentbus.thread.closed" not in signals


@pytest.mark.asyncio
async def test_ephemeral_close_failure_emits_close_failed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ephemeral + delivery 2xx + close 4xx → close.failed emitted; delivery OK."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record(
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
            "bus_lifecycle": "ephemeral",
        }
    )

    _patch_client(monkeypatch, _make_close_handler(captured, status=503))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is True
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.sent" in signals
    assert "pipeline.dispatch.delivery.close.failed" in signals
    assert "mcp.agentbus.thread.closed" not in signals


@pytest.mark.asyncio
async def test_persistent_does_not_close_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default persistent lifecycle → close is never called."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record()  # bus_lifecycle defaults to persistent (not set)

    _patch_client(monkeypatch, _make_close_handler(captured))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is None


# ---------------------------------------------------------------------------
# Hints propagation — envelope and to_dict
# ---------------------------------------------------------------------------


def test_build_envelope_includes_hints_when_present() -> None:
    """hints are serialized into the delivery envelope so bus subscribers don't
    need a second round-trip to the poll endpoint for degradation advisories."""
    import json

    hint = {
        "type": "output_short",
        "output_tokens": 0,
        "provider": "openai",
        "reason": "0 output tokens",
        "suggestion": "retry with an Anthropic model or another available provider",
    }
    record = _make_record(
        result=PipelineExecutionResult(
            content="",
            model="openai/gpt-5.4",
            usage={"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
            duration_s=1.0,
            reasoning=None,
            hints=[hint],
        )
    )
    envelope = json.loads(_build_envelope(record))
    assert "hints" in envelope, "hints key must appear in envelope when non-empty"
    assert envelope["hints"] == [hint]


def test_build_envelope_omits_hints_when_empty() -> None:
    """hints key is absent from the envelope when there are no hints."""
    import json

    record = _make_record()  # result has no hints
    envelope = json.loads(_build_envelope(record))
    assert "hints" not in envelope, (
        "hints key must be absent when result.hints is empty"
    )


def test_record_to_dict_propagates_hints() -> None:
    """PipelineExecutionResult.hints round-trips through to_dict as result.hints."""
    hint = {"type": "output_short", "output_tokens": 0, "provider": "openai"}
    record = _make_record(
        result=PipelineExecutionResult(
            content="",
            model="openai/gpt-5.4",
            usage={"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
            duration_s=1.0,
            reasoning=None,
            hints=[hint],
        )
    )
    d = record.to_dict()
    assert d["result"]["hints"] == [hint]


def test_record_to_dict_hints_empty_when_none() -> None:
    """result.hints is [] in to_dict when PipelineExecutionResult.hints is None."""
    record = _make_record()  # default result has hints=None
    d = record.to_dict()
    assert d["result"]["hints"] == [], "to_dict must normalize None hints to []"
