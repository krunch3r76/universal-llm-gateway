"""Tests for virtual pipeline chat-completion lifecycle helpers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from fastapi.responses import Response

from systems.pipeline.core.dag import PipelineExecutionError
from systems.pipeline.core.execution.errors import FrontierDispatchExhaustedError
from systems.proxy.stargate.requests.pipeline_lifecycle import (
    _build_recoverable_failure_response,
    _is_recoverable_frontier_exhaustion,
    _pipeline_error_mode,
    _pipeline_execution_error_detail,
    _wrap_pipeline_response_as_sse,
)


@dataclass
class _FakeContext:
    selected_model: str = "orion-agent-high"
    original_request: dict[str, Any] | None = None
    http_request: Any = None

    def __post_init__(self) -> None:
        if self.original_request is None:
            self.original_request = {"stream": True}


def _wrapped_frontier_exhaustion() -> PipelineExecutionError:
    cause = FrontierDispatchExhaustedError(
        execution_id="exec-123",
        agent="gatherer",
        model="openai/gpt-5.4",
        provider="openai",
        turns_used=4,
        tool_calls_made=10,
        finish_reason="tool_calls",
        block_reason=None,
        exhaustion_summary={
            "execution_id": "exec-123",
            "turns_used": 4,
            "tool_calls_made": 10,
            "exhaustion_reason": "repeated_section_not_found",
            "failed_tools": [
                {
                    "tool": "fs.md_read",
                    "code": "section_not_found",
                    "target": "docs/foo.md#Missing",
                    "count": 2,
                    "suggested_next_action": "Run md_list first.",
                }
            ],
            "suggested_continuation": ["Run md_list first."],
        },
    )
    try:
        raise cause
    except FrontierDispatchExhaustedError as exc:
        raise PipelineExecutionError("Step 'respond' failed") from exc


def test_wrapped_frontier_exhaustion_preserves_recoverable_code() -> None:
    try:
        _wrapped_frontier_exhaustion()
    except PipelineExecutionError as exc:
        detail = _pipeline_execution_error_detail(exc, context=_FakeContext())

    assert detail["code"] == "frontier_dispatch_exhausted"
    assert detail["recoverable"] is True
    assert detail["execution_id"] == "exec-123"
    assert detail["exhaustion_summary"]["failed_tools"][0]["code"] == (
        "section_not_found"
    )


def test_streaming_pipeline_error_defaults_to_assistant_message_response() -> None:
    context = _FakeContext()
    assert _pipeline_error_mode(context) == "assistant_message"

    try:
        _wrapped_frontier_exhaustion()
    except PipelineExecutionError as exc:
        detail = _pipeline_execution_error_detail(exc, context=context)

    assert _is_recoverable_frontier_exhaustion(detail) is True
    response = _build_recoverable_failure_response(
        context,
        detail,
        headers={"X-Pipeline-Execution-Id": "exec-123"},
    )
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["outcome"] == "recoverable_failure"
    assert body["failure"]["code"] == "frontier_dispatch_exhausted"
    assert (
        "I hit the frontier tool-loop budget"
        in body["choices"][0]["message"]["content"]
    )


def _make_pipeline_json_response(
    *,
    content: str = "hello",
    pipeline_id: str = "Zorgath",
    exec_id: str = "exec-abc",
) -> Response:
    """Build a JSON Response in the shape ResponseBuilder.build_response emits."""
    body = {
        "id": "chatcmpl-pipeline-abc123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": pipeline_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        "resolved_models": ["hermes-3-llama-3-1-70b-uncensored-q4-k-m-65536-hybrid"],
    }
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200,
        headers={"X-Pipeline-Execution-Id": exec_id},
    )


def _drain_streaming_body(streaming_response: Any) -> list[str]:
    """Drive the StreamingResponse generator to completion; return chunks as strings."""
    chunks: list[str] = []

    async def drive() -> None:
        async for chunk in streaming_response.body_iterator:
            if isinstance(chunk, (bytes, bytearray)):
                chunks.append(chunk.decode("utf-8"))
            else:
                chunks.append(chunk)

    asyncio.run(drive())
    return chunks


def test_wrap_pipeline_response_emits_two_sse_frames_plus_done() -> None:
    json_response = _make_pipeline_json_response(
        content="hi", pipeline_id="Zorgath", exec_id="exec-abc"
    )

    streaming = _wrap_pipeline_response_as_sse(json_response)

    assert streaming.media_type == "text/event-stream"
    assert streaming.headers.get("X-Pipeline-Execution-Id") == "exec-abc"

    chunks = _drain_streaming_body(streaming)

    # Two data frames plus DONE sentinel — final-message single-chunk SSE shape.
    assert len(chunks) == 3
    assert chunks[0].startswith("data: ") and chunks[0].endswith("\n\n")
    assert chunks[1].startswith("data: ") and chunks[1].endswith("\n\n")
    assert chunks[2] == "data: [DONE]\n\n"

    content_chunk = json.loads(chunks[0][len("data: ") :].strip())
    assert content_chunk["object"] == "chat.completion.chunk"
    assert content_chunk["model"] == "Zorgath"
    assert content_chunk["choices"][0]["delta"] == {
        "role": "assistant",
        "content": "hi",
    }
    assert content_chunk["choices"][0]["finish_reason"] is None

    finish_chunk = json.loads(chunks[1][len("data: ") :].strip())
    assert finish_chunk["choices"][0]["delta"] == {}
    assert finish_chunk["choices"][0]["finish_reason"] == "stop"


def test_wrap_pipeline_response_omits_execution_header_when_absent() -> None:
    # ResponseBuilder always sets the header, but defensive behavior matters:
    # a future caller that drops the header should not produce a header with an
    # empty string value.
    body = {
        "id": "chatcmpl-pipeline-xyz",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "Zorgath",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    json_response = Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=200,
        # No X-Pipeline-Execution-Id header.
    )

    streaming = _wrap_pipeline_response_as_sse(json_response)

    assert "x-pipeline-execution-id" not in {k.lower() for k in streaming.headers}
