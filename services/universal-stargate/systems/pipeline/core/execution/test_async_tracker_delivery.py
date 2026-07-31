"""B3 regression tests — agent-bus result delivery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from systems.pipeline.core.execution.async_tracker import (
    DeliveryState,
    PipelineExecutionError,
    PipelineExecutionRecord,
    PipelineExecutionResult,
    delivery_hooks,
)
from systems.pipeline.core.execution.async_tracker_delivery import (
    DeliveryOutcome,
    _build_envelope,
    deliver_result,
)
from systems.pipeline.core.execution.async_tracker_delivery.envelope import (
    _extract_pointer_summary,
)
from systems.pipeline.core.execution.async_tracker_delivery.sidecar import SidecarResult


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
        "systems.pipeline.core.execution.async_tracker_delivery.agent_bus_http.make_async_client",
        lambda *_a, **_k: httpx.AsyncClient(
            transport=transport, base_url="http://localhost"
        ),
    )


def _patch_sidecar_ok(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uri: str = "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md",
    sha256: str = "abc123def456",
) -> None:
    async def _write_sidecar(record, *, content, thread, subject, oversized):
        return SidecarResult(
            uri=uri,
            sha256=sha256,
            body_chars=len(content),
        )

    monkeypatch.setattr(
        "systems.pipeline.core.execution.async_tracker_delivery.on_behalf.write_on_behalf_sidecar",
        _write_sidecar,
    )


def _patch_sidecar_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _write_sidecar_fail(record, *, content, thread, subject, oversized):
        return None

    monkeypatch.setattr(
        "systems.pipeline.core.execution.async_tracker_delivery.on_behalf.write_on_behalf_sidecar",
        _write_sidecar_fail,
    )


@pytest.mark.asyncio
async def test_deliver_sent_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = _FakeBus()
    record = _make_record()
    record.bus_lifecycle = "persistent"
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
    """Explicit persistent lifecycle → close is never called."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record(
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
            "bus_subject": "done",
            "bus_lifecycle": "persistent",
        }
    )

    _patch_client(monkeypatch, _make_close_handler(captured))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is None


# ---------------------------------------------------------------------------
# Friction 16985 — caller-facing delivery recovery metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivery_outcome_carries_sidecar_fields_on_oversized_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    over_limit = "x" * 8_001
    record = _make_to_thread_record(content=over_limit)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "sidecar"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )
    assert outcome.content_sha256 == "abc123def456"


@pytest.mark.asyncio
async def test_delivery_outcome_carries_thread_on_inline_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="Short review body.")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "inline"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )


@pytest.mark.asyncio
async def test_delivery_outcome_carries_sidecar_uri_on_post_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    over_limit = "x" * 8_001
    record = _make_to_thread_record(content=over_limit)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured, post_status=503))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "failed"
    assert outcome.failure_reason == "post_503"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )


def test_delivery_state_envelope_sidecar_delivered() -> None:
    envelope = DeliveryState(
        status="delivered",
        mode="sidecar",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
        content_sha256="deadbeef",
    ).to_dict("exec-9")

    assert envelope["attempted"] is True
    assert envelope["recovery"]["kind"] == "sidecar"
    assert "fs(cortex, op=read, path=notes/x.md)" in envelope["recovery"]["hint"]


def test_delivery_state_envelope_inline_delivered() -> None:
    with_uri = DeliveryState(
        status="delivered",
        mode="inline",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
    ).to_dict("exec-9")
    assert with_uri["recovery"]["kind"] == "sidecar"

    without_uri = DeliveryState(
        status="delivered",
        mode="inline",
        thread="1051",
        sidecar_uri=None,
    ).to_dict("exec-9")
    assert without_uri["recovery"]["kind"] == "thread"
    assert without_uri["recovery"]["hint"] == "Delivered to agent-bus thread 1051."


def test_delivery_state_envelope_failed_with_sidecar() -> None:
    envelope = DeliveryState(
        status="failed",
        sidecar_uri="cortex://notes/x.md",
        failure_reason="post_503",
    ).to_dict("exec-9")

    assert envelope["recovery"]["kind"] == "sidecar"
    assert "Bus delivery failed" in envelope["recovery"]["hint"]


def test_delivery_state_envelope_failed_no_sidecar() -> None:
    envelope = DeliveryState(
        status="failed",
        sidecar_uri=None,
        failure_reason="post_401",
    ).to_dict("exec-9")

    assert envelope["recovery"]["kind"] == "pipeline_result"
    assert "pipeline(op=result, execution_id=exec-9)" in envelope["recovery"]["hint"]


def test_record_to_dict_delivery_none_by_default() -> None:
    assert _make_record().to_dict()["delivery"] is None


def test_record_to_dict_serializes_delivery_envelope() -> None:
    record = _make_to_thread_record()
    record.delivery = DeliveryState(
        status="delivered",
        mode="sidecar",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
        content_sha256="deadbeef",
    )
    delivery = record.to_dict()["delivery"]

    assert delivery is not None
    assert delivery["recovery"]["execution_id"] == record.execution_id
    assert delivery["status"] == "delivered"
    assert delivery["mode"] == "sidecar"
    assert delivery["thread"] == "1051"
    assert delivery["sidecar_uri"] == "cortex://notes/x.md"
    assert delivery["content_sha256"] == "deadbeef"
    assert delivery["recovery"]["kind"] == "sidecar"


def test_delivery_state_from_outcome_maps_fields() -> None:
    outcome = DeliveryOutcome(
        status="failed",
        failure_reason="post_503",
        thread="1051",
        delivery_mode="inline",
    )
    state = delivery_hooks._delivery_state_from_outcome(outcome)

    assert isinstance(state, DeliveryState)
    assert state.status == "failed"
    assert state.failure_reason == "post_503"
    assert state.thread == "1051"
    assert state.mode == "inline"


@pytest.mark.asyncio
async def test_run_delivery_sets_record_delivery_on_failed_demote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery_hooks, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery_hooks, "_schedule_journal", lambda *_a, **_k: None)

    async def _sender(_record: PipelineExecutionRecord) -> DeliveryOutcome:
        return DeliveryOutcome(
            status="failed",
            failure_reason="post_503",
            thread="1051",
        )

    tracker = SimpleNamespace(_delivery_sender=_sender)
    record = _make_to_thread_record()

    await delivery_hooks._run_delivery_with_outcome(tracker, record)

    assert record.status == "failed"
    assert record.delivery is not None
    delivery = record.to_dict()["delivery"]
    assert delivery is not None
    assert delivery["recovery"]["kind"] == "pipeline_result"
    assert record.execution_id in delivery["recovery"]["hint"]


@pytest.mark.asyncio
async def test_run_delivery_sets_record_delivery_on_sidecar_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery_hooks, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery_hooks, "_schedule_journal", lambda *_a, **_k: None)

    async def _sender(_record: PipelineExecutionRecord) -> DeliveryOutcome:
        return DeliveryOutcome(
            status="delivered",
            delivery_mode="sidecar",
            thread="1051",
            sidecar_uri="cortex://notes/x.md",
            content_sha256="deadbeef",
        )

    tracker = SimpleNamespace(_delivery_sender=_sender)
    record = _make_to_thread_record()

    await delivery_hooks._run_delivery_with_outcome(tracker, record)

    assert record.status == "completed"
    delivery = record.to_dict()["delivery"]
    assert delivery is not None
    assert delivery["recovery"]["kind"] == "sidecar"
    assert delivery["mode"] == "sidecar"


@pytest.mark.asyncio
async def test_default_ephemeral_closes_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitted bus_lifecycle on record defaults to ephemeral → close called."""
    bus = _FakeBus()
    captured: dict[str, object] = {}
    record = _make_record(
        result_delivery={
            "bus_thread": "626",
            "bus_from_agent": "gatherer",
            "bus_to_agent": "cursor",
            "bus_subject": "done",
        }
    )

    _patch_client(monkeypatch, _make_close_handler(captured))

    await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is True


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


# ---------------------------------------------------------------------------
# On-behalf delivery (op="to_thread") — 2026-05-22 architectural fix
# ---------------------------------------------------------------------------


def _make_to_thread_record(
    *,
    content: str = "Reply body text.",
    from_agent: str = "reviewer",
    caller_agent: str = "claude-web",
    target_thread: str = "1051",
    reply_subject: str | None = None,
) -> PipelineExecutionRecord:
    """Bus-mode record with all on-behalf delivery fields populated."""
    return PipelineExecutionRecord(
        execution_id="exec-to-thread-1",
        pipeline="frontier-dispatch",
        status="completed",
        started_at="2026-05-22T22:34:26Z",
        started_at_monotonic=0.0,
        completed_at="2026-05-22T22:37:05Z",
        completed_at_monotonic=159.0,
        result=PipelineExecutionResult(
            content=content,
            model="openai/gpt-5.5",
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
            },
            duration_s=159.0,
            reasoning=None,
        ),
        error=None,
        result_delivery=None,
        caller_agent=caller_agent,
        output_contract="thread",
        target_thread=target_thread,
        op="to_thread",
        from_agent=from_agent,
        reply_subject=reply_subject,
    )


def _make_thread_aware_transport(
    captured: dict[str, Any],
    *,
    last_turn_from: str = "claude-web",
    post_status: int = 201,
    close_status: int = 200,
) -> httpx.MockTransport:
    """Mock /threads/{id} GET (last_turn_from), /turns POST, and /close PATCH."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/threads/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": "1051",
                    "status": "active",
                    "turn_count": 5,
                    "last_turn_from": last_turn_from,
                },
            )
        if request.method == "POST" and request.url.path == "/turns":
            captured["post_path"] = request.url.path
            captured["post_body"] = json.loads(request.content)
            return httpx.Response(post_status, json={"id": 6, "turn_number": 6})
        if request.method == "PATCH" and "/close" in request.url.path:
            captured["close_called"] = True
            captured["close_path"] = request.url.path
            captured["close_body"] = json.loads(request.content)
            return httpx.Response(close_status, json={})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_on_behalf_post_delivered_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """op=to_thread + non-empty content + 2xx POST → delivered.

    Regression: replaces the polling-based contract that failed structurally
    for mcp=False dispatches (exec 9d970982) and tool-budget-exhausted
    dispatches (exec 8c1df5d3) on 2026-05-22.
    """
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="Short review body.")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "inline"
    assert captured["post_path"] == "/turns"
    body = captured["post_body"]
    assert body["from"] == "reviewer"
    assert body["to"] == "claude-web"  # caller_agent preferred over last_turn_from
    assert body["thread"] == "1051"
    assert body["body"].startswith("Short review body.")
    assert "Durable copy:" in body["body"]
    assert "allow_long_body" not in body  # under 1500 chars
    assert record.thread_reply_observed_at is not None
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.sent" in signals


@pytest.mark.asyncio
async def test_on_behalf_post_sets_allow_long_body_above_briefing_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content above the briefing-rule threshold opts into allow_long_body=true."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    long_body = "x" * 2000  # > _BUS_BRIEFING_RULE_CHARS (1500)
    record = _make_to_thread_record(content=long_body)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert captured["post_body"]["allow_long_body"] is True


@pytest.mark.asyncio
async def test_on_behalf_post_rejects_content_over_bus_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized content posts a relocation pointer when sidecar write succeeds."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    over_limit = "x" * 8_001
    record = _make_to_thread_record(content=over_limit)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "sidecar"
    assert captured["post_path"] == "/turns"
    body = captured["post_body"]["body"]
    assert "Full reply relocated to cortex" in body
    assert "cortex://notes/system/threads/" in body
    assert "abc123def456" in body
    sent = next(
        e
        for e in bus.events
        if getattr(e, "signal", None) == "pipeline.dispatch.delivery.sent"
    )
    assert sent.payload["delivery_mode"] == "sidecar"
    assert sent.payload["sidecar_status"] == "ok"


@pytest.mark.asyncio
async def test_on_behalf_within_limit_writes_sidecar_and_posts_inline_with_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="Within limit body.")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "inline"
    assert captured["post_body"]["body"].startswith("Within limit body.")
    assert "Durable copy:" in captured["post_body"]["body"]
    sent = next(
        e
        for e in bus.events
        if getattr(e, "signal", None) == "pipeline.dispatch.delivery.sent"
    )
    assert sent.payload["delivery_mode"] == "inline"
    assert sent.payload["sidecar_status"] == "ok"
    assert sent.payload["content_sha256"] == "abc123def456"


@pytest.mark.asyncio
async def test_on_behalf_within_limit_sidecar_fail_posts_inline_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="Degraded inline body.")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_fail(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "inline"
    assert captured["post_body"]["body"] == "Degraded inline body."
    sent = next(
        e
        for e in bus.events
        if getattr(e, "signal", None) == "pipeline.dispatch.delivery.sent"
    )
    assert sent.payload["sidecar_status"] == "failed"
    assert sent.payload["sidecar_uri"] is None


@pytest.mark.asyncio
async def test_on_behalf_over_limit_writes_sidecar_and_posts_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    content = "# Oversized report\n\n" + ("detail. " * 2_000)
    record = _make_to_thread_record(content=content)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "sidecar"
    body = captured["post_body"]["body"]
    assert "Oversized report" in body
    assert "Full reply relocated to cortex" in body
    assert "allow_long_body" not in captured["post_body"]


@pytest.mark.asyncio
async def test_on_behalf_over_limit_sidecar_fail_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="x" * 8_001)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_fail(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "failed"
    assert outcome.failure_reason == "sidecar_write_failed"
    assert "post_path" not in captured
    failed = next(
        e
        for e in bus.events
        if getattr(e, "signal", None) == "pipeline.dispatch.delivery.failed"
    )
    assert "sidecar_write_failed" in failed.payload["error_preview"]


def test_extract_pointer_summary() -> None:
    content = "---\ntitle: ignored\n---\n\n# Main heading\n\nFirst sentence. Second."
    summary = _extract_pointer_summary(content)
    assert summary is not None
    assert "Main heading" in summary
    assert "First sentence" in summary

    assert _extract_pointer_summary("   ") is None

    long = "# Title\n\n" + ("word " * 200)
    capped = _extract_pointer_summary(long, max_chars=50)
    assert capped is not None
    assert len(capped) <= 50


@pytest.mark.asyncio
async def test_on_behalf_post_skips_empty_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty content skips POST and returns skipped (record is already failed)."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="   ")  # whitespace-only

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "skipped"
    assert outcome.failure_reason == "empty_content"
    assert "post_path" not in captured


@pytest.mark.asyncio
async def test_on_behalf_post_falls_back_to_last_turn_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When caller_agent is unset, the thread's last_turn_from becomes to_agent."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(caller_agent=None)

    _patch_client(
        monkeypatch,
        _make_thread_aware_transport(captured, last_turn_from="skeptic"),
    )
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert captured["post_body"]["to"] == "skeptic"


@pytest.mark.asyncio
async def test_on_behalf_post_fails_when_no_to_agent_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolved to_agent (no caller_agent, no thread last_turn_from) fails fast."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(caller_agent=None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json={"id": "1051", "status": "active", "last_turn_from": None}
            )
        captured["post_attempted"] = True
        return httpx.Response(201, json={"id": 1})

    _patch_client(monkeypatch, httpx.MockTransport(handler))

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "failed"
    assert outcome.failure_reason == "unresolved_to_agent"
    assert "post_attempted" not in captured


@pytest.mark.asyncio
async def test_on_behalf_post_demotes_record_on_post_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST 5xx on bus-mode delivery returns failed with post_{code} reason."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record()

    _patch_client(monkeypatch, _make_thread_aware_transport(captured, post_status=503))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "failed"
    assert outcome.failure_reason == "post_503"
    assert record.thread_reply_observed_at is None
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "pipeline.dispatch.delivery.failed" in signals


@pytest.mark.asyncio
async def test_on_behalf_post_uses_caller_subject_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reply_subject on the record propagates into the posted turn subject."""
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(reply_subject="Re: plan-promotion review")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    await deliver_result(record, event_bus=_FakeBus(), auth_token="secret")
    await asyncio.sleep(0)

    assert captured["post_body"]["subject"] == "Re: plan-promotion review"


@pytest.mark.asyncio
async def test_on_behalf_ephemeral_closes_thread_on_2xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """team-dispatch one-shots: ephemeral bus_lifecycle closes after on-behalf POST."""
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record()
    record.bus_lifecycle = "ephemeral"

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert captured.get("close_called") is True
    assert captured["close_path"] == "/threads/1051/close"
    signals = [getattr(e, "signal", None) for e in bus.events]
    assert "mcp.agentbus.thread.closed" in signals


@pytest.mark.asyncio
async def test_on_behalf_persistent_skips_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persistent lifecycle leaves the thread open after delivery (close-on-read)."""
    captured: dict[str, Any] = {}
    record = _make_to_thread_record()
    record.bus_lifecycle = "persistent"

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    await deliver_result(record, event_bus=_FakeBus(), auth_token="secret")
    await asyncio.sleep(0)

    assert captured.get("close_called") is None


# ---------------------------------------------------------------------------
# Friction 16985 — caller-facing delivery recovery metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delivery_outcome_carries_sidecar_fields_on_oversized_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    over_limit = "x" * 8_001
    record = _make_to_thread_record(content=over_limit)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "sidecar"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )
    assert outcome.content_sha256 == "abc123def456"


@pytest.mark.asyncio
async def test_delivery_outcome_carries_thread_on_inline_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    record = _make_to_thread_record(content="Short review body.")

    _patch_client(monkeypatch, _make_thread_aware_transport(captured))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "delivered"
    assert outcome.delivery_mode == "inline"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )


@pytest.mark.asyncio
async def test_delivery_outcome_carries_sidecar_uri_on_post_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    captured: dict[str, Any] = {}
    over_limit = "x" * 8_001
    record = _make_to_thread_record(content=over_limit)

    _patch_client(monkeypatch, _make_thread_aware_transport(captured, post_status=503))
    _patch_sidecar_ok(monkeypatch)

    outcome = await deliver_result(record, event_bus=bus, auth_token="secret")
    await asyncio.sleep(0)

    assert outcome.status == "failed"
    assert outcome.failure_reason == "post_503"
    assert outcome.thread == "1051"
    assert outcome.sidecar_uri == (
        "cortex://notes/system/threads/1051-reviewer-reply-execution-exec-to.md"
    )


def test_delivery_state_envelope_sidecar_delivered() -> None:
    envelope = DeliveryState(
        status="delivered",
        mode="sidecar",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
        content_sha256="deadbeef",
    ).to_dict("exec-9")

    assert envelope["attempted"] is True
    assert envelope["recovery"]["kind"] == "sidecar"
    assert "fs(cortex, op=read, path=notes/x.md)" in envelope["recovery"]["hint"]


def test_delivery_state_envelope_inline_delivered() -> None:
    with_uri = DeliveryState(
        status="delivered",
        mode="inline",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
    ).to_dict("exec-9")
    assert with_uri["recovery"]["kind"] == "sidecar"

    without_uri = DeliveryState(
        status="delivered",
        mode="inline",
        thread="1051",
        sidecar_uri=None,
    ).to_dict("exec-9")
    assert without_uri["recovery"]["kind"] == "thread"
    assert without_uri["recovery"]["hint"] == "Delivered to agent-bus thread 1051."


def test_delivery_state_envelope_failed_with_sidecar() -> None:
    envelope = DeliveryState(
        status="failed",
        sidecar_uri="cortex://notes/x.md",
        failure_reason="post_503",
    ).to_dict("exec-9")

    assert envelope["recovery"]["kind"] == "sidecar"
    assert "Bus delivery failed" in envelope["recovery"]["hint"]


def test_delivery_state_envelope_failed_no_sidecar() -> None:
    envelope = DeliveryState(
        status="failed",
        sidecar_uri=None,
        failure_reason="post_401",
    ).to_dict("exec-9")

    assert envelope["recovery"]["kind"] == "pipeline_result"
    assert "pipeline(op=result, execution_id=exec-9)" in envelope["recovery"]["hint"]


def test_record_to_dict_delivery_none_by_default() -> None:
    assert _make_record().to_dict()["delivery"] is None


def test_record_to_dict_serializes_delivery_envelope() -> None:
    record = _make_to_thread_record()
    record.delivery = DeliveryState(
        status="delivered",
        mode="sidecar",
        thread="1051",
        sidecar_uri="cortex://notes/x.md",
        content_sha256="deadbeef",
    )
    delivery = record.to_dict()["delivery"]

    assert delivery is not None
    assert delivery["recovery"]["execution_id"] == record.execution_id
    assert delivery["status"] == "delivered"
    assert delivery["mode"] == "sidecar"
    assert delivery["thread"] == "1051"
    assert delivery["sidecar_uri"] == "cortex://notes/x.md"
    assert delivery["content_sha256"] == "deadbeef"
    assert delivery["recovery"]["kind"] == "sidecar"


def test_delivery_state_from_outcome_maps_fields() -> None:
    outcome = DeliveryOutcome(
        status="failed",
        failure_reason="post_503",
        thread="1051",
        delivery_mode="inline",
    )
    state = delivery_hooks._delivery_state_from_outcome(outcome)

    assert isinstance(state, DeliveryState)
    assert state.status == "failed"
    assert state.failure_reason == "post_503"
    assert state.thread == "1051"
    assert state.mode == "inline"


@pytest.mark.asyncio
async def test_run_delivery_sets_record_delivery_on_failed_demote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery_hooks, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery_hooks, "_schedule_journal", lambda *_a, **_k: None)

    async def _sender(_record: PipelineExecutionRecord) -> DeliveryOutcome:
        return DeliveryOutcome(
            status="failed",
            failure_reason="post_503",
            thread="1051",
        )

    tracker = SimpleNamespace(_delivery_sender=_sender)
    record = _make_to_thread_record()

    await delivery_hooks._run_delivery_with_outcome(tracker, record)

    assert record.status == "failed"
    assert record.delivery is not None
    delivery = record.to_dict()["delivery"]
    assert delivery is not None
    assert delivery["recovery"]["kind"] == "pipeline_result"
    assert record.execution_id in delivery["recovery"]["hint"]


@pytest.mark.asyncio
async def test_run_delivery_sets_record_delivery_on_sidecar_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delivery_hooks, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(delivery_hooks, "_schedule_journal", lambda *_a, **_k: None)

    async def _sender(_record: PipelineExecutionRecord) -> DeliveryOutcome:
        return DeliveryOutcome(
            status="delivered",
            delivery_mode="sidecar",
            thread="1051",
            sidecar_uri="cortex://notes/x.md",
            content_sha256="deadbeef",
        )

    tracker = SimpleNamespace(_delivery_sender=_sender)
    record = _make_to_thread_record()

    await delivery_hooks._run_delivery_with_outcome(tracker, record)

    assert record.status == "completed"
    delivery = record.to_dict()["delivery"]
    assert delivery is not None
    assert delivery["recovery"]["kind"] == "sidecar"
    assert delivery["mode"] == "sidecar"
