"""Execution wrapper for virtual pipeline chat-completions."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from sse import format_sse
from universal_logging import get_logger

from systems.pipeline.core.handlers.protocol import StepOutput

from .pipeline_request_events import (
    emit_pipeline_completed_events,
    emit_pipeline_failed_events,
    emit_pipeline_routed_events,
)

if TYPE_CHECKING:
    from systems.proxy.core.nonstreaming.preparer import RequestContext

    from ..proxy import StargateProxy

logger = get_logger(__name__)


def _pipeline_error_mode(context: RequestContext) -> str:
    """Return ``strict`` or ``assistant_message`` for recoverable failures."""
    http_request = context.http_request
    if http_request is not None:
        header_mode = http_request.headers.get("x-stargate-pipeline-error-mode")
        if header_mode in {"strict", "assistant_message"}:
            return header_mode
    requested = context.original_request.get("pipeline_error_mode")
    if requested in {"strict", "assistant_message"}:
        return str(requested)
    if context.original_request.get("stream") is True:
        return "assistant_message"
    return "strict"


def _is_recoverable_frontier_exhaustion(error_detail: dict[str, Any]) -> bool:
    return (
        error_detail.get("code") == "frontier_dispatch_exhausted"
        and error_detail.get("recoverable") is True
    )


def _format_recoverable_message(error_detail: dict[str, Any]) -> str:
    summary = error_detail.get("exhaustion_summary")
    if not isinstance(summary, dict):
        summary = {}
    error_code = error_detail.get("code", "frontier_dispatch_exhausted")
    turns_used = error_detail.get("turns_used", summary.get("turns_used", "unknown"))
    tool_calls_made = error_detail.get(
        "tool_calls_made",
        summary.get("tool_calls_made", "unknown"),
    )
    exhaustion_reason = summary.get(
        "exhaustion_reason",
        "tool_loop_budget_exhausted",
    )
    lines = [
        "I hit the frontier tool-loop budget before producing a final answer.",
        "",
        (
            "This is recoverable: reply with what you want me to do next, "
            "and I can continue from this diagnostic instead of leaving the "
            "chat in an HTTP error state."
        ),
        "",
        f"- error_code: {error_code}",
        f"- execution_id: {error_detail.get('execution_id', 'unknown')}",
        f"- turns_used: {turns_used}",
        f"- tool_calls_made: {tool_calls_made}",
        f"- exhaustion_reason: {exhaustion_reason}",
    ]
    failed = summary.get("failed_tools")
    if isinstance(failed, list) and failed:
        lines.extend(["", "Most relevant tool friction:"])
        for item in failed[:3]:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"{item.get('tool', 'tool')} "
                f"{item.get('code', 'tool_error')} "
                f"count={item.get('count', '?')} "
                f"target={item.get('target', '')!r}: "
                f"{item.get('suggested_next_action', '')}"
            )
    suggested = summary.get("suggested_continuation")
    if isinstance(suggested, list) and suggested:
        lines.extend(["", "Suggested continuation:"])
        for item in suggested[:3]:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _terminal_step_output(context: RequestContext) -> StepOutput | None:
    """Return the terminal-step ``StepOutput`` surfaced by the executor, if any.

    ``PipelineExecutor.execute()`` writes ``pipeline_spec`` and
    ``_pipeline_outputs`` onto the proxy ``RequestContext`` only when the
    terminal step produced a streaming ``StepOutput`` (i.e.
    ``output.stream is not None``). Returns ``None`` otherwise so the caller
    falls through to the buffered path.

    See ``plan:pipeline-terminal-passthrough-streaming`` Phase 4.
    """
    pipeline_spec = getattr(context, "pipeline_spec", None)
    if pipeline_spec is None:
        return None
    pipeline_outputs = getattr(context, "_pipeline_outputs", None)
    if pipeline_outputs is None:
        return None
    output = pipeline_outputs.get(pipeline_spec.output)
    if isinstance(output, StepOutput):
        return output
    return None


def _build_passthrough_streaming_response(
    *,
    proxy: StargateProxy,
    context: RequestContext,
    model_id: str,
    terminal_output: StepOutput,
    pipeline_id: str,
    execution_id: str | None,
    start_time: float,
) -> StreamingResponse:
    """Build a StreamingResponse that drives the terminal step's chunk iterator.

    Re-frames each upstream chunk's ``model`` field to the pipeline ID
    (matching ``ResponseBuilder`` buffered-path convention), formats via
    ``libs/sse``'s ``format_sse``, accumulates content + final ``usage`` for
    the stream-end snapshot event, and emits the lifecycle events at
    stream-end via ``_emit_streaming_completion``.

    Error handling distinguishes pre-first-yield (raised to caller, no
    partial bytes on wire) from mid-stream (failure event emitted with
    ``partial_content=True``, stream terminates without ``[DONE]``). See
    Phase 3 carry-over #7 in the Phase 4 kickoff for the rationale and
    Phase 2's failure-mode vocabulary contract.
    """
    accumulated_content_parts: list[str] = []
    final_usage: dict[str, Any] | None = None

    async def gen() -> AsyncIterator[str]:
        nonlocal final_usage
        chunks_yielded = 0
        assert terminal_output.stream is not None  # guard; eligibility checked
        try:
            async for chunk in terminal_output.stream:
                # Re-frame ``model`` to the pipeline ID (matches
                # ResponseBuilder.build_response buffered-path convention,
                # which sets ``body["model"] = pipeline.id``).
                chunk["model"] = pipeline_id

                # Accumulate content for the stream-end snapshot event.
                choices = chunk.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content_part = delta.get("content")
                    if isinstance(content_part, str):
                        accumulated_content_parts.append(content_part)

                # Capture final usage (vLLM emits on the terminal chunk when
                # ``stream_options.include_usage`` is set; Phase 2 contract).
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    final_usage = usage

                yield format_sse(chunk)
                chunks_yielded += 1
        except BaseException as exc:
            # Mid-stream: emit pipeline.failed with partial_content=True and
            # close the connection (no [DONE]). Pre-first-yield: emit failure
            # event then re-raise so the lifecycle's outer handler returns a
            # clean 5xx to the client (no bytes on the wire yet).
            await _emit_streaming_completion(
                proxy=proxy,
                context=context,
                model_id=model_id,
                content="".join(accumulated_content_parts),
                usage=final_usage,
                start_time=start_time,
                error=exc,
            )
            if chunks_yielded == 0:
                raise
            return
        # Clean stream-end: emit lifecycle completion + DONE sentinel.
        await _emit_streaming_completion(
            proxy=proxy,
            context=context,
            model_id=model_id,
            content="".join(accumulated_content_parts),
            usage=final_usage,
            start_time=start_time,
            error=None,
        )
        yield "data: [DONE]\n\n"

    headers: dict[str, str] = {}
    if execution_id:
        headers["X-Pipeline-Execution-Id"] = execution_id

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=headers,
    )


async def _emit_streaming_completion(
    *,
    proxy: StargateProxy,
    context: RequestContext,
    model_id: str,
    content: str,
    usage: dict[str, Any] | None,
    start_time: float,
    error: BaseException | None,
) -> None:
    """Emit ``pipeline.completed`` / ``pipeline.failed`` at stream-end.

    On success: builds a synthetic ``Response`` carrying the aggregated
    ``content`` + final ``usage``, in the shape ``_response_snapshot`` reads
    (``choices[0].message.content`` and ``usage``), and forwards to
    ``emit_pipeline_completed_events`` so observability sees the same
    snapshot shape as the buffered path.

    On error: extracts Phase 2 failure-mode vocabulary fields from
    ``ProxyClientError.detail`` when present (``code``, ``content_type``,
    ``body_preview``, ``stall_timeout_seconds``, ``chunks_received``,
    ``partial_content``) and emits ``pipeline.failed``. Falls back to
    ``streaming_upstream_error`` when no ``detail.code`` is available.
    """
    if error is None:
        synthetic_body = {
            "id": f"chatcmpl-pipeline-stream-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(start_time),
            "model": str(context.selected_model),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage or {},
        }
        synthetic_response = Response(
            content=json.dumps(synthetic_body),
            media_type="application/json",
            status_code=200,
        )
        await emit_pipeline_completed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            response=synthetic_response,
            start_time=start_time,
        )
        return

    error_detail: dict[str, Any] = {
        "error_type": type(error).__name__,
        "message": str(error),
        "pipeline_id": str(context.selected_model),
        "partial_content": bool(content),
    }
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict):
        code = detail.get("code")
        if isinstance(code, str):
            error_detail["code"] = code
        for key in (
            "content_type",
            "body_preview",
            "stall_timeout_seconds",
            "chunks_received",
        ):
            if key in detail:
                error_detail[key] = detail[key]
        if "partial_content" in detail:
            error_detail["partial_content"] = bool(detail["partial_content"])
    error_detail.setdefault("code", "streaming_upstream_error")

    await emit_pipeline_failed_events(
        proxy.event_bus,
        context,
        model_id=model_id,
        gateway_url=proxy.gateway_url,
        error=error,
        error_detail=error_detail,
    )


def _wrap_pipeline_response_as_sse(
    json_response: Response,
) -> StreamingResponse:
    """Convert a pipeline JSON Response into single-chunk SSE for streaming clients.

    Pipelines today buffer the inner inference call (``stream=False`` enforced in
    ``proxy_client.chat_completion``) and ``ResponseBuilder`` emits one
    ``application/json`` ``chat.completion`` body. OpenAI-compatible clients that
    set ``stream: true`` (Open WebUI, others) engage an SSE parser that cannot
    consume a JSON body; the assistant turn is silently dropped.

    This wrapper re-frames that buffered output as a two-frame SSE response
    (``chat.completion.chunk`` with content + finish chunk + ``[DONE]`` sentinel)
    so streaming clients render correctly. The body is identical; only the
    transport changes.

    For per-token passthrough on eligible single-step terminal pipelines, see
    ``plan:pipeline-terminal-passthrough-streaming``.
    """
    body = json.loads(json_response.body)
    base = {
        "id": body["id"],
        "object": "chat.completion.chunk",
        "created": body["created"],
        "model": body["model"],
    }
    content_chunk = {
        **base,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": body["choices"][0]["message"]["content"],
                },
                "finish_reason": None,
            }
        ],
    }
    finish_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }

    async def gen() -> Any:
        yield format_sse(content_chunk)
        yield format_sse(finish_chunk)
        yield "data: [DONE]\n\n"

    # Preserve pipeline execution header only; StreamingResponse sets its own
    # Content-Type and omits Content-Length (chunked transfer encoding).
    headers: dict[str, str] = {}
    exec_id = json_response.headers.get("X-Pipeline-Execution-Id")
    if exec_id:
        headers["X-Pipeline-Execution-Id"] = exec_id

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=headers,
    )


def _build_recoverable_failure_response(
    context: RequestContext,
    error_detail: dict[str, Any],
    *,
    headers: dict[str, str],
) -> Response:
    body = {
        "id": f"chatcmpl-pipeline-recoverable-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(context.selected_model),
        "outcome": "recoverable_failure",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": _format_recoverable_message(error_detail),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "failure": error_detail,
    }
    return Response(
        content=json.dumps(body),
        status_code=200,
        media_type="application/json",
        headers=headers,
    )


def _pipeline_execution_error_detail(
    exc: BaseException,
    *,
    context: RequestContext,
) -> dict[str, Any]:
    from systems.pipeline.core.executor import _normalize_pipeline_exception

    code, message, data = _normalize_pipeline_exception(exc)
    error_detail: dict[str, Any] = {
        "error_type": "PipelineExecutionError",
        "retryable": True,
        "message": message,
        "code": code,
        "pipeline_id": str(context.selected_model),
    }
    if isinstance(data, dict):
        error_detail.update(data)
        error_detail["pipeline_id"] = str(context.selected_model)
        error_detail.setdefault("message", message)
        error_detail.setdefault("code", code)
    return error_detail


async def execute_pipeline_chat_completion(
    proxy: StargateProxy,
    context: RequestContext,
) -> Response:
    """Execute a virtual pipeline model and emit request lifecycle events."""
    from systems.pipeline.core.dag import PipelineExecutionError
    from systems.pipeline.core.execution.errors import PipelineError

    model_id = str(context.requested_model or context.selected_model)
    start_time = time.time()
    execution_id: str | None = None
    exec_header: dict[str, str] = {}

    await emit_pipeline_routed_events(
        proxy.event_bus,
        context,
        model_id=model_id,
        gateway_url=proxy.gateway_url,
    )
    try:
        response = await proxy.pipeline_executor.execute(context)

        # Terminal-passthrough streaming: when the executor surfaced a
        # streaming StepOutput on the terminal step (via
        # ``context.pipeline_spec`` + ``context._pipeline_outputs``), build a
        # StreamingResponse driven by the chunk iterator. Lifecycle events
        # emit at stream-end inside the generator. Falls through to the
        # buffered path otherwise.
        # See ``plan:pipeline-terminal-passthrough-streaming`` Phase 4.
        terminal_output = _terminal_step_output(context)
        if terminal_output is not None and terminal_output.stream is not None:
            pipeline_spec = context.pipeline_spec  # type: ignore[attr-defined]
            execution_id_header = response.headers.get("X-Pipeline-Execution-Id")
            return _build_passthrough_streaming_response(
                proxy=proxy,
                context=context,
                model_id=model_id,
                terminal_output=terminal_output,
                pipeline_id=pipeline_spec.id,
                execution_id=execution_id_header,
                start_time=start_time,
            )

        await emit_pipeline_completed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            response=response,
            start_time=start_time,
        )
        if context.original_request.get("stream") is True:
            response = _wrap_pipeline_response_as_sse(response)
        return response
    except PipelineError as exc:
        execution_id = getattr(exc, "execution_id", None)
        exec_header = {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
        error_detail = exc.to_dict()
        if execution_id:
            error_detail["execution_id"] = execution_id
        error_detail["pipeline_id"] = str(context.selected_model)
        logger.error(
            "Pipeline execution failed: %s - %s",
            error_detail.get("error_type"),
            str(exc),
            exc_info=True,
        )
        await emit_pipeline_failed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            error=exc,
            error_detail=error_detail,
        )
        if (
            _is_recoverable_frontier_exhaustion(error_detail)
            and _pipeline_error_mode(context) == "assistant_message"
        ):
            return _build_recoverable_failure_response(
                context,
                error_detail,
                headers=exec_header,
            )
        raise HTTPException(
            status_code=500,
            detail=error_detail,
            headers=exec_header,
        ) from exc
    except PipelineExecutionError as exc:
        error_detail = _pipeline_execution_error_detail(exc, context=context)
        execution_id = getattr(exc, "execution_id", None) or error_detail.get(
            "execution_id"
        )
        exec_header = {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
        if execution_id:
            error_detail["execution_id"] = execution_id
        logger.error(
            "Pipeline execution error: %s (execution_id=%s)",
            exc,
            execution_id,
            exc_info=True,
        )
        await emit_pipeline_failed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            error=exc,
            error_detail=error_detail,
        )
        if (
            _is_recoverable_frontier_exhaustion(error_detail)
            and _pipeline_error_mode(context) == "assistant_message"
        ):
            return _build_recoverable_failure_response(
                context,
                error_detail,
                headers=exec_header,
            )
        raise HTTPException(
            status_code=500,
            detail=error_detail,
            headers=exec_header,
        ) from exc
    except HTTPException as exc:
        error_detail = exc.detail if isinstance(exc.detail, dict) else None
        await emit_pipeline_failed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            error=exc,
            error_detail=error_detail,
        )
        raise
    except Exception as exc:
        error_detail = {
            "error_type": type(exc).__name__,
            "retryable": True,
            "message": f"Internal server error: {exc}",
            "code": "internal_server_error",
            "pipeline_id": str(context.selected_model),
        }
        logger.error("Pipeline execution error: %s", exc, exc_info=True)
        await emit_pipeline_failed_events(
            proxy.event_bus,
            context,
            model_id=model_id,
            gateway_url=proxy.gateway_url,
            error=exc,
            error_detail=error_detail,
        )
        raise HTTPException(
            status_code=500,
            detail=error_detail,
        ) from exc
