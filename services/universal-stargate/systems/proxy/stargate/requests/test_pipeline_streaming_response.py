"""Tests for terminal-passthrough streaming response construction.

Covers Phase 4 of ``plan:pipeline-terminal-passthrough-streaming``:

- ``_build_passthrough_streaming_response`` re-frames upstream chunk
  ``model`` fields to the pipeline ID, accumulates content + final
  ``usage`` for the snapshot event, distinguishes pre-first-yield from
  mid-stream errors, and emits ``pipeline.completed``/``pipeline.failed``
  events at stream-end.
- ``execute_pipeline_chat_completion`` selects the buffered path (with
  optional single-chunk SSE wrap) when the executor does NOT surface
  streaming attributes on the context.

Companion sidecar:
``cortex:notes/system/threads/pipeline-terminal-passthrough-streaming-arc-phase-4.md``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import Response, StreamingResponse

from src.scheduling.events import (
    REQUEST_COMPLETED,
    REQUEST_FAILED,
    REQUEST_PROCESSING,
    REQUEST_SNAPSHOT_COMPLETED,
    REQUEST_SNAPSHOT_FAILED,
    REQUEST_SNAPSHOT_ROUTED,
)
from systems.pipeline.core.handlers.protocol import StepOutput
from systems.proxy.stargate.requests.pipeline_lifecycle import (
    _build_passthrough_streaming_response,
    _terminal_step_output,
    execute_pipeline_chat_completion,
)

# --- Fakes ---------------------------------------------------------------


class _FakeEventBus:
    """Captures published events for assertion."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish_nowait(self, event: Any) -> None:
        self.published.append(event)


@dataclass
class _FakePipelineSpec:
    id: str = "Zorgath"
    output: str = "respond"


@dataclass
class _FakeProxy:
    event_bus: _FakeEventBus = field(default_factory=_FakeEventBus)
    gateway_url: str = "http://gateway.test"
    pipeline_executor: Any = None


@dataclass
class _FakeContext:
    request_id: str = "req-test-abc"
    selected_model: str = "Zorgath"
    requested_model: str = "Zorgath"
    original_request: dict[str, Any] = field(default_factory=lambda: {"stream": True})
    request_profile: str | None = None
    pipeline_execution_id: str | None = None
    pipeline_step_id: str | None = None
    http_request: Any = None
    pipeline_spec: Any = None
    _pipeline_outputs: Any = None


class _ChunkStream:
    """AsyncIterator yielding a fixed list of dict chunks.

    Optionally raises ``exc`` at index ``raise_at`` to model mid-stream
    failure modes. ``raise_at=0`` with no chunks yielded yet simulates
    a pre-first-yield error (Phase 2 vocabulary: ``upstream_non_streaming``,
    ``empty_stream``, pre-stream 4xx).
    """

    def __init__(
        self,
        chunks: list[dict[str, Any]],
        *,
        raise_at: int | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._chunks = list(chunks)
        self._idx = 0
        self._raise_at = raise_at
        self._exc = exc

    def __aiter__(self) -> _ChunkStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if (
            self._raise_at is not None
            and self._idx == self._raise_at
            and self._exc is not None
        ):
            raise self._exc
        if self._idx >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._idx]
        self._idx += 1
        return chunk


class _FakeProxyClientError(Exception):
    """Stand-in for ``ProxyClientError`` carrying Phase 2's ``detail`` shape."""

    def __init__(self, message: str, detail: dict[str, Any]) -> None:
        super().__init__(message)
        self.detail = detail


def _drain_streaming(
    streaming: StreamingResponse,
) -> tuple[list[str], BaseException | None]:
    """Drive the StreamingResponse generator to completion.

    Returns ``(chunks, error)``. If the underlying generator raises,
    ``error`` is the exception and ``chunks`` is everything yielded
    before the raise. On clean exit ``error`` is ``None``.
    """
    chunks: list[str] = []
    captured: list[BaseException] = []

    async def drive() -> None:
        try:
            async for chunk in streaming.body_iterator:
                if isinstance(chunk, bytes | bytearray):
                    chunks.append(chunk.decode("utf-8"))
                else:
                    chunks.append(chunk)
        except BaseException as exc:  # noqa: BLE001 — test driver boundary
            captured.append(exc)

    asyncio.run(drive())
    return chunks, captured[0] if captured else None


def _make_chunk(
    model: str, content: str, *, finish: str | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-upstream-1",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish,
            }
        ],
    }


# --- Tests: _build_passthrough_streaming_response -----------------------


def test_passthrough_response_yields_re_framed_chunks() -> None:
    chunks_in = [
        _make_chunk("hermes-3-llama", "Hello"),
        _make_chunk("hermes-3-llama", " world", finish="stop"),
    ]
    stream = _ChunkStream(chunks_in)
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-test-abc",
        start_time=1700000000.0,
    )

    assert isinstance(streaming, StreamingResponse)
    assert streaming.media_type == "text/event-stream"
    assert streaming.headers.get("X-Pipeline-Execution-Id") == "exec-test-abc"

    out_chunks, err = _drain_streaming(streaming)
    assert err is None
    # 2 data frames + [DONE] sentinel
    assert len(out_chunks) == 3

    first = json.loads(out_chunks[0][len("data: ") :].strip())
    second = json.loads(out_chunks[1][len("data: ") :].strip())
    assert first["model"] == "Zorgath"
    assert second["model"] == "Zorgath"
    assert first["choices"][0]["delta"]["content"] == "Hello"
    assert second["choices"][0]["delta"]["content"] == " world"


def test_passthrough_response_appends_done_sentinel_on_clean_exit() -> None:
    stream = _ChunkStream([_make_chunk("upstream-model", "ok", finish="stop")])
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-clean",
        start_time=1700000000.0,
    )

    out_chunks, err = _drain_streaming(streaming)
    assert err is None
    assert out_chunks[-1] == "data: [DONE]\n\n"


def test_passthrough_response_skips_done_on_error() -> None:
    """Mid-stream error: [DONE] is NOT yielded; generator terminates cleanly."""
    err = _FakeProxyClientError(
        "upstream broke",
        detail={"code": "mid_stream_network_error", "partial_content": True},
    )
    stream = _ChunkStream(
        [_make_chunk("upstream-model", "partial")],
        raise_at=1,
        exc=err,
    )
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-mid-err",
        start_time=1700000000.0,
    )

    out_chunks, drain_err = _drain_streaming(streaming)
    # 1 chunk yielded before the mid-stream raise; no [DONE].
    assert len(out_chunks) == 1
    assert all(chunk != "data: [DONE]\n\n" for chunk in out_chunks)
    # Mid-stream errors do NOT propagate to the driver — the generator
    # catches, emits pipeline.failed, and returns cleanly so the client
    # sees connection close (partial response in their buffer).
    assert drain_err is None


def test_passthrough_response_aggregates_content_for_snapshot() -> None:
    chunks_in = [
        _make_chunk("upstream", "Hello"),
        _make_chunk("upstream", " "),
        _make_chunk("upstream", "world", finish="stop"),
    ]
    stream = _ChunkStream(chunks_in)
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-snap",
        start_time=1700000000.0,
    )
    _drain_streaming(streaming)

    snapshots = [
        ev
        for ev in proxy.event_bus.published
        if ev.signal == REQUEST_SNAPSHOT_COMPLETED
    ]
    assert len(snapshots) == 1
    assert snapshots[0].payload["content"] == "Hello world"


def test_passthrough_response_passes_final_usage_to_snapshot() -> None:
    final_usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    chunks_in = [
        _make_chunk("upstream", "hi", finish="stop"),
        # Final chunk carries usage (vLLM stream_options.include_usage convention)
        {
            "id": "chatcmpl-upstream-1",
            "object": "chat.completion.chunk",
            "model": "upstream",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": final_usage,
        },
    ]
    stream = _ChunkStream(chunks_in)
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-usage",
        start_time=1700000000.0,
    )
    _drain_streaming(streaming)

    snapshots = [
        ev
        for ev in proxy.event_bus.published
        if ev.signal == REQUEST_SNAPSHOT_COMPLETED
    ]
    assert len(snapshots) == 1
    assert snapshots[0].payload["usage"] == final_usage


def test_passthrough_response_emits_failed_event_on_mid_stream_error() -> None:
    err = _FakeProxyClientError(
        "stream stalled",
        detail={"code": "stream_stalled", "stall_timeout_seconds": 30},
    )
    chunks_in = [_make_chunk("upstream", "partial")]
    stream = _ChunkStream(chunks_in, raise_at=1, exc=err)
    terminal = StepOutput(raw="", stream=stream)
    proxy = _FakeProxy()
    context = _FakeContext()

    streaming = _build_passthrough_streaming_response(
        proxy=proxy,
        context=context,
        model_id="Zorgath",
        terminal_output=terminal,
        pipeline_id="Zorgath",
        execution_id="exec-fail-mid",
        start_time=1700000000.0,
    )
    _drain_streaming(streaming)

    failed = [ev for ev in proxy.event_bus.published if ev.signal == REQUEST_FAILED]
    snapshot_failed = [
        ev for ev in proxy.event_bus.published if ev.signal == REQUEST_SNAPSHOT_FAILED
    ]
    assert len(failed) == 1
    assert len(snapshot_failed) == 1
    # Phase 2 vocabulary surfaces through to the pipeline.failed event
    failed_payload = failed[0].payload
    assert failed_payload["error_code"] == "stream_stalled"
    assert failed_payload["error_data"] is not None
    assert failed_payload["error_data"]["partial_content"] is True
    assert failed_payload["error_data"]["stall_timeout_seconds"] == 30


# --- Tests: execute_pipeline_chat_completion branch selection ----------


class _FakeBufferedExecutor:
    """Executor that returns a buffered JSON Response without surfacing streaming attrs.

    Mirrors the non-streaming path: ``execute()`` returns the Response
    produced by ``ResponseBuilder.build_response`` and writes nothing to
    ``context.pipeline_spec`` / ``context._pipeline_outputs``.
    """

    def __init__(self, response: Response) -> None:
        self._response = response

    async def execute(self, context: Any) -> Response:
        return self._response


def _buffered_pipeline_response(
    *, content: str = "hi", exec_id: str = "exec-bu"
) -> Response:
    body = {
        "id": "chatcmpl-pipeline-buffered",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "Zorgath",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "resolved_models": ["hermes-3-llama"],
    }
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200,
        headers={"X-Pipeline-Execution-Id": exec_id},
    )


def test_lifecycle_falls_back_to_wrap_when_terminal_output_has_no_stream() -> None:
    """No streaming attrs surfaced + ``stream: true`` → single-chunk SSE wrap."""
    response = _buffered_pipeline_response(content="hi", exec_id="exec-wrap")
    proxy = _FakeProxy(pipeline_executor=_FakeBufferedExecutor(response))
    context = _FakeContext(original_request={"stream": True})

    result = asyncio.run(execute_pipeline_chat_completion(proxy, context))

    # Lifecycle wrapped as SSE — same StreamingResponse as the existing
    # ineligible-pipeline fallback path produces.
    assert isinstance(result, StreamingResponse)
    assert result.media_type == "text/event-stream"
    assert result.headers.get("X-Pipeline-Execution-Id") == "exec-wrap"

    chunks, err = _drain_streaming(result)
    assert err is None
    # Single-chunk wrap = 2 SSE data frames + [DONE].
    assert len(chunks) == 3
    assert chunks[-1] == "data: [DONE]\n\n"


def test_lifecycle_uses_buffered_path_when_stream_false() -> None:
    """No streaming attrs + ``stream`` not requested → raw buffered Response."""
    response = _buffered_pipeline_response(content="hi", exec_id="exec-raw")
    proxy = _FakeProxy(pipeline_executor=_FakeBufferedExecutor(response))
    context = _FakeContext(original_request={"stream": False})

    result = asyncio.run(execute_pipeline_chat_completion(proxy, context))

    # The original buffered Response is returned untouched (no wrap, no
    # StreamingResponse). Routed + completed events emit synchronously.
    assert isinstance(result, Response)
    assert not isinstance(result, StreamingResponse)
    assert result is response

    published_signals = {ev.signal for ev in proxy.event_bus.published}
    assert REQUEST_PROCESSING in published_signals
    assert REQUEST_SNAPSHOT_ROUTED in published_signals
    assert REQUEST_COMPLETED in published_signals
    assert REQUEST_SNAPSHOT_COMPLETED in published_signals


# --- Bonus: _terminal_step_output helper --------------------------------


def test_terminal_step_output_returns_none_when_attrs_absent() -> None:
    """No pipeline_spec / _pipeline_outputs on context → helper returns None."""
    context = _FakeContext()
    assert _terminal_step_output(context) is None


def test_terminal_step_output_returns_stepoutput_when_surfaced() -> None:
    """Executor-surfaced StepOutput is returned via the terminal step name lookup."""
    pipeline_spec = _FakePipelineSpec(id="Zorgath", output="respond")
    stream = _ChunkStream([])
    terminal = StepOutput(raw="", stream=stream)
    context = _FakeContext(
        pipeline_spec=pipeline_spec,
        _pipeline_outputs={"respond": terminal},
    )
    assert _terminal_step_output(context) is terminal
