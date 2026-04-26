"""Non-streaming chat completion execution path."""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request
from universal_logging import get_logger

from src.core.errors import (
    GatewayError,
    ModelLoadingError,
    SyntaxErrorException,
    WorkerInitializationError,
    create_error_response,
)
from src.core.events.types import RequestInferenceStarted

from .disconnect import inference_with_disconnect_watch
from .events import emit_inference_failed_nowait, emit_request_queued_nowait
from .openai_errors import create_openai_error_response
from .response_build import build_completion_response, resolve_gateway_url
from .runtime_errors import handle_runtime_error

logger = get_logger(__name__)
api_logger = get_logger("universal_llm_gateway.api")


async def generate_non_streaming_response(
    worker_controller: Any,
    model_id: str,
    messages: Any,
    request_id: str,
    correlation_id: str,
    start_time: float,
    event_bus: Any,
    generation_params: dict,
    timeout_hint: float | None = None,
    request: Request | None = None,
):
    """Generate non-streaming completion response."""
    try:
        logger.info("Processing inference request for model: %s", model_id)

        await emit_request_queued_nowait(
            event_bus, model_id, request_id, messages, generation_params, stream=False
        )

        if not await worker_controller.ensure_model_loaded(model_id):
            if not worker_controller.auto_load_on_request:
                return create_openai_error_response(
                    status_code=400,
                    message=f"Model '{model_id}' is not loaded",
                    error_type="invalid_request_error",
                    error_code="model_not_loaded",
                    param="model",
                    suggestion=f"POST /api/v1/models/{model_id}/load",
                )
            from src.core.resources import resource_tracker

            tracker = resource_tracker.get_resource_tracker()
            model_info = tracker.get_model_info(model_id)
            if model_info and model_info.status.name == "FAILED":
                error_msg = model_info.error_message or "Unknown error"
                context = {"request_id": request_id, "model_id": model_id}
                raise ModelLoadingError(f"Model failed: {error_msg}", context)

            raise ModelLoadingError(
                f"Model '{model_id}' is not available after auto-load attempt.",
                {"request_id": request_id, "model_id": model_id},
            )

        await event_bus.publish_nowait(
            RequestInferenceStarted(
                request_id=request_id,
                model_id=model_id,
                gateway_url=resolve_gateway_url(request),
                correlation_id=correlation_id,
            )
        )

        inference_coro = worker_controller.generate_chat_completion(
            model_id=model_id,
            messages=messages,
            correlation_id=correlation_id,
            _request_id=request_id,
            _timeout_hint=timeout_hint,
            **generation_params,
        )

        if request is None:
            completion_result = await inference_coro
        else:
            completion_result = await inference_with_disconnect_watch(
                inference_coro, request, worker_controller, model_id, request_id
            )

        response = build_completion_response(completion_result, model_id)
        response_time_ms = (time.time() - start_time) * 1000
        api_logger.info(
            "POST /v1/chat/completions - 200 - %.2fms - model:%s",
            response_time_ms,
            model_id,
        )
        return response

    except (
        ModelLoadingError,
        WorkerInitializationError,
        GatewayError,
        SyntaxErrorException,
    ) as e:
        logger.error("Gateway error: %s", e)
        context = {"request_id": request_id}
        raise create_error_response(e, 500, context)

    except RuntimeError as e:
        response_time_ms = (time.time() - start_time) * 1000
        return handle_runtime_error(e, model_id, request_id, response_time_ms)

    except HTTPException:
        raise

    except Exception as e:
        await emit_inference_failed_nowait(event_bus, model_id, request_id, str(e))
        api_logger.error("Unexpected error in chat completion: %s", e)
        return create_openai_error_response(
            status_code=500,
            message="Internal server error occurred",
            error_type="server_error",
            error_code="unexpected_error",
        )
