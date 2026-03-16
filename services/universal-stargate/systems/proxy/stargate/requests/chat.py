from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request
from fastapi.responses import Response
from universal_logging import get_logger

from src.scheduling.events import (
    RequestProcessing,
    RequestProfileResolved,
    RequestSnapshotReceived,
    RequestSnapshotRouted,
)

# Import from service root to avoid "beyond top-level package" error
from src.schemas.chat_completion import ChatCompletionRequest

from .retry import execute_with_retry

if TYPE_CHECKING:
    from ..proxy import StargateProxy

logger = get_logger(__name__)


async def process_chat_completion(
    proxy: StargateProxy,
    request: Request,
    chat_request: ChatCompletionRequest,
    model_override: str | None,
    profile_override: str | None,
    disable_profile: bool,
    skip_token_counting: bool | None,
) -> Response:
    """Process a chat completion request with unified capacity retry."""
    target_model = model_override or chat_request.model
    is_pipeline = bool(proxy.pipeline_registry) and proxy.pipeline_registry.is_pipeline(
        target_model
    )

    if is_pipeline and chat_request.messages:
        # Preserve full history before truncation for pipeline context.messages
        request.state.pipeline_full_messages = [
            msg.model_dump() if hasattr(msg, "model_dump") else msg
            for msg in chat_request.messages
        ]

        last_user_msg = next(
            (msg for msg in reversed(chat_request.messages) if msg.role == "user"), None
        )
        if last_user_msg:
            logger.info(
                "🔪 Pipeline request: Truncating %d messages to last user message only",
                len(chat_request.messages),
            )
            chat_request = chat_request.model_copy(update={"messages": [last_user_msg]})

    context = await proxy.request_preparer.prepare_request(
        request,
        chat_request,
        model_override=model_override,
        profile_override=profile_override,
        disable_profile=disable_profile,
        is_pipeline=is_pipeline,
        skip_token_counting=skip_token_counting,
    )

    if proxy.event_bus:
        try:
            msgs = [
                m.model_dump() if hasattr(m, "model_dump") else m
                for m in (chat_request.messages or [])
            ]
            await proxy.event_bus.publish_async_nowait(
                RequestSnapshotReceived(
                    request_id=context.request_id,
                    model_id=target_model,
                    messages=msgs[:10],
                    is_pipeline=is_pipeline,
                )
            )
        except Exception:
            logger.exception("Failed to publish RequestSnapshotReceived event")

    if proxy.monitor:
        try:
            profile_name = getattr(context, "request_profile", None)
            await proxy.monitor.log_request_info(
                original_request=context.original_request,
                request_id=context.request_id,
                selected_model=str(context.selected_model),
                profile_name=profile_name,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to send early request_info event: %s", exc)

    if is_pipeline:
        logger.info("Routing to pipeline executor: %s", context.selected_model)
        from systems.pipeline.core.dag import PipelineExecutionError
        from systems.pipeline.core.execution.errors import PipelineError

        execution_id: str | None = None
        exec_header: dict[str, str] = {}
        try:
            return await proxy.pipeline_executor.execute(context)
        except PipelineError as exc:
            execution_id = getattr(exc, "execution_id", None)
            exec_header = (
                {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
            )
            error_dict = exc.to_dict()
            if execution_id:
                error_dict["execution_id"] = execution_id
            logger.error(
                "Pipeline execution failed: %s - %s",
                error_dict.get("error_type"),
                str(exc),
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": error_dict,
                    "pipeline_id": context.selected_model,
                },
                headers=exec_header,
            ) from exc
        except PipelineExecutionError as exc:
            execution_id = getattr(exc, "execution_id", None)
            exec_header = (
                {"X-Pipeline-Execution-Id": execution_id} if execution_id else {}
            )
            error_detail: dict[str, object] = {
                "message": f"Internal server error: {exc}",
                "type": "internal_error",
                "code": "internal_server_error",
                "operation": "chat_completions",
            }
            if execution_id:
                error_detail["execution_id"] = execution_id
            logger.error(
                "Pipeline execution error: %s (execution_id=%s)",
                exc,
                execution_id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": error_detail},
                headers=exec_header,
            ) from exc

    model_id = str(context.selected_model)
    start_time = time.time()

    if proxy.event_bus:
        try:
            profile_name = getattr(context, "request_profile", None)
            if profile_name:
                await proxy.event_bus.publish_async_nowait(
                    RequestProfileResolved(
                        request_id=context.request_id,
                        model_id=model_id,
                        profile_name=profile_name,
                    )
                )
            await proxy.event_bus.publish_async_nowait(
                RequestProcessing(
                    request_id=context.request_id,
                    gateway_url=proxy.gateway_url,
                    model_id=model_id,
                )
            )
            await proxy.event_bus.publish_async_nowait(
                RequestSnapshotRouted(
                    request_id=context.request_id,
                    model_id=model_id,
                    gateway_id=getattr(context, "target_gateway_id", "") or "",
                    profile_name=profile_name,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to emit REQUEST_PROCESSING event: %s", exc)

    return await execute_with_retry(proxy, context, model_id, request, start_time)
