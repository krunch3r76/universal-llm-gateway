"""Chat completion dispatch hub.

Sole entry point for all ``/v1/chat/completions`` requests on Stargate.
Performs request preparation (via RequestPreparer), emits lifecycle events
(RequestSnapshotReceived, RequestProcessing, RequestSnapshotRouted), and
delegates execution to either the pipeline executor or the retry loop
(retry.py).  Contains no retry or transformation logic itself — those
responsibilities live in retry.py and mode_transforms.py respectively.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import Response
from universal_logging import get_logger

from src.scheduling.events import (
    RequestAliasResolved,
    RequestProcessing,
    RequestProfileResolved,
    RequestSnapshotReceived,
    RequestSnapshotRouted,
)

# Import from service root to avoid "beyond top-level package" error
from src.schemas.chat_completion import ChatCompletionRequest

from .pipeline_lifecycle import execute_pipeline_chat_completion
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
    """Process a chat completion request.

    Handles preparation, event emission, and execution.

    Orchestration flow:
    1. Pipeline detection — if the target model is a pipeline, truncates
       messages to last user message and preserves full history in request state.
    2. Request preparation — delegates to proxy.request_preparer.prepare_request
       which handles model validation, profile resolution, and transformations.
    3. Event emission — publishes RequestSnapshotReceived, RequestProfileResolved,
       RequestProcessing, and RequestSnapshotRouted for observability.
    4. Execution — routes to pipeline executor (if pipeline) or execute_with_retry
       (retry.py) for standard inference requests.

    Returns the HTTP Response (streaming or non-streaming) from the executor.
    """
    requested_model = model_override or chat_request.model
    effective_model_override = model_override
    if requested_model and getattr(proxy, "persona_alias_manager", None):
        alias = proxy.persona_alias_manager.get(requested_model)
        if alias is not None:
            effective_model_override = alias.backing_model

    target_model_for_pipeline_check = effective_model_override or chat_request.model
    is_pipeline = (
        proxy.is_pipeline_system_ready
        and target_model_for_pipeline_check is not None
        and proxy.pipeline_registry.is_pipeline(target_model_for_pipeline_check)
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
        model_override=effective_model_override,
        profile_override=profile_override,
        disable_profile=disable_profile,
        is_pipeline=is_pipeline,
        skip_token_counting=skip_token_counting,
        requested_model=requested_model,
    )

    if proxy.event_bus:
        try:
            msgs = [
                m.model_dump() if hasattr(m, "model_dump") else m
                for m in (chat_request.messages or [])
            ]
            await proxy.event_bus.publish_nowait(
                RequestSnapshotReceived(
                    request_id=context.request_id,
                    model_id=context.requested_model,
                    messages=msgs[:10],
                    is_pipeline=is_pipeline,
                )
            )
        except Exception as exc:
            logger.exception(
                "Failed to publish RequestSnapshotReceived event (%s)",
                type(exc).__name__,
            )

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
            logger.warning("Failed to send early request_info event: %s", exc)

    if is_pipeline:
        logger.info("Routing to pipeline executor: %s", context.selected_model)
        return await execute_pipeline_chat_completion(proxy, context)

    model_id = str(context.selected_model)
    start_time = time.time()

    if proxy.event_bus:
        try:
            profile_name = getattr(context, "request_profile", None)
            if getattr(context, "persona_alias_id", None) and getattr(
                context, "persona_backing_model", None
            ):
                await proxy.event_bus.publish_nowait(
                    RequestAliasResolved(
                        request_id=context.request_id,
                        alias_id=context.persona_alias_id,
                        backing_model_id=context.persona_backing_model,
                    )
                )
            if profile_name:
                await proxy.event_bus.publish_nowait(
                    RequestProfileResolved(
                        request_id=context.request_id,
                        model_id=model_id,
                        profile_name=profile_name,
                    )
                )
            await proxy.event_bus.publish_nowait(
                RequestProcessing(
                    request_id=context.request_id,
                    gateway_url=proxy.gateway_url,
                    model_id=model_id,
                )
            )
            await proxy.event_bus.publish_nowait(
                RequestSnapshotRouted(
                    request_id=context.request_id,
                    model_id=model_id,
                    gateway_id=getattr(context, "target_gateway_id", ""),
                    profile_name=profile_name,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to emit REQUEST_PROCESSING event: %s",
                exc,
                exc_info=True,
            )

    return await execute_with_retry(proxy, context, model_id, request, start_time)
