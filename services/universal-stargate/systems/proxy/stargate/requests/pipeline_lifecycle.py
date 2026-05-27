"""Execution wrapper for virtual pipeline chat-completions."""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from sse import format_sse
from universal_logging import get_logger

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
