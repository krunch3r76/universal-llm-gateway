"""
Chat completions endpoint - /v1/chat/completions

ARCHITECTURAL INVARIANT: Pure Passthrough (Gateway/Workers)
∀ client_params: gateway_params = client_params ∖ {routing_metadata}

Gateway and Workers are PURE PASSTHROUGH:
- ¬validation: no parameter validation (engines validate)
- ¬transformation: no parameter modification
- ¬defaults: never add default values
- ¬override: never change client values

ONLY Stargate (proxy layer) may modify generation parameters.
This centralizes parameter logic at the orchestration layer.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from universal_logging import format_json_for_log, get_logger

from src.core.errors import (
    ErrorCode,
    GatewayError,
    ModelLoadingError,
    SyntaxErrorException,
    WorkerInitializationError,
    create_error_response,
    is_connection_error,
    is_crash_error,
)
from src.core.gateway_config import GatewayConfig
from src.core.model_registry import ModelRegistry
from src.routers.dependencies import (
    get_event_bus,
    get_gateway_config,
    get_model_registry,
    get_worker_controller,
)
from src.schemas.chat_completion import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
)

# Remove import - truncation now automatic
from .events import emit_inference_failed_nowait, emit_request_queued_nowait
from .model_resolution import resolve_model_id
from .openai_errors import (
    create_model_crash_error_response,
    create_openai_error_response,
)
from .stream import generate_streaming_response

router = APIRouter()
logger = get_logger(__name__)
api_logger = get_logger("universal_llm_gateway.api")


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    tags=["OpenAI Compatible"],
)
async def create_chat_completion(
    request: Request,
    completion_request: ChatCompletionRequest,
    model: str | None = Query(None, description="Model ID (query param override)"),
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
    gateway_config: GatewayConfig = Depends(get_gateway_config),
    event_bus=Depends(get_event_bus),
):
    """OpenAI-compatible chat completion endpoint."""
    start_time = time.time()
    request_id = str(uuid.uuid4())
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())

    # Extract timeout hint from upstream (federation/pipeline)
    timeout_hint = None
    timeout_hint_header = request.headers.get("X-Request-Timeout")
    if timeout_hint_header:
        try:
            timeout_hint = float(timeout_hint_header)
            logger.debug(f"[{correlation_id}] Received timeout hint: {timeout_hint}s")
        except ValueError:
            logger.warning(
                f"[{correlation_id}] Invalid X-Request-Timeout header: "
                f"{timeout_hint_header}"
            )

    # Log validated request
    completion_dict = completion_request.model_dump(exclude_unset=True)
    logger.info(
        f"[{correlation_id}] DEBUG VALIDATED REQUEST: "
        f"{format_json_for_log(completion_dict)}"  # Unicode + automatic truncation
    )

    # Resolve and validate model ID (preserves synthetic IDs like "model-65536")
    model_name = resolve_model_id(
        model_override=model,
        request_model=completion_request.model,
        model_registry=model_registry,
    )

    # Extract generation parameters (exclude_unset for passthrough)
    # INVARIANT: Pure passthrough - Gateway forwards params unchanged to worker/engine
    # No validation, no defaults, no transformation (Stargate's responsibility)
    generation_params = _build_generation_params(completion_request)
    stream = completion_request.stream or False

    # Log generation parameters explicitly for verification
    logger.info(
        f"[{correlation_id}] 🎛️  Generation parameters extracted: "
        f"{format_json_for_log(generation_params)}"
    )

    # Handle messages OR prompt (worker accepts both)
    if completion_request.messages:
        messages_or_prompt = [msg.model_dump() for msg in completion_request.messages]
    elif completion_request.prompt:
        messages_or_prompt = completion_request.prompt
    else:
        raise HTTPException(
            status_code=400,
            detail={"error": {"message": "Either messages or prompt is required"}},
        )

    # ModelId stringifies to original, preserving context suffixes
    model_id_str = str(model_name)

    # Handle streaming vs non-streaming
    if stream:
        return StreamingResponse(
            generate_streaming_response(
                worker_controller=worker_controller,
                model_id=model_id_str,
                messages=messages_or_prompt,
                event_bus=event_bus,
                correlation_id=correlation_id,
                timeout_hint=timeout_hint,
                **generation_params,
            ),
            media_type="application/newline-delimited-json",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        return await _generate_non_streaming_response(
            worker_controller=worker_controller,
            model_id=model_id_str,
            messages=messages_or_prompt,
            request_id=request_id,
            correlation_id=correlation_id,
            start_time=start_time,
            event_bus=event_bus,
            gateway_config=gateway_config,
            generation_params=generation_params,
            timeout_hint=timeout_hint,
        )


def _build_generation_params(
    completion_request: ChatCompletionRequest,
) -> dict:
    """
    Extract generation parameters from request (pure passthrough).

    INVARIANT: Pure passthrough - ¬defaults, ¬override, ¬validation

    Removes only routing metadata (model, messages, stream) that workers
    don't need. All generation parameters (temperature, max_tokens, etc.)
    pass through unchanged.

    Args:
        completion_request: The completion request

    Returns:
        dict: Generation parameters for worker (passthrough only)
    """
    generation_params = completion_request.model_dump(exclude_unset=True)
    for key in ["model", "messages", "prompt", "stream"]:
        generation_params.pop(key, None)
    return generation_params


def _build_completion_response(
    completion_result: dict,
    model_id: str,
) -> ChatCompletionResponse:
    """
    Build ChatCompletionResponse from worker result.

    Args:
        completion_result: Worker completion result dict
        model_id: Model identifier

    Returns:
        ChatCompletionResponse: OpenAI-format response
    """
    content = completion_result.get("content", "")
    response_message = ChatMessage(role="assistant", content=content)
    choice = ChatCompletionChoice(
        index=0, message=response_message, finish_reason="stop"
    )
    usage = ChatCompletionUsage(
        prompt_tokens=completion_result.get("prompt_tokens", 0),
        completion_tokens=completion_result.get("completion_tokens", 0),
        total_tokens=completion_result.get("total_tokens", 0),
    )
    return ChatCompletionResponse(model=model_id, choices=[choice], usage=usage)


async def _generate_non_streaming_response(
    worker_controller,
    model_id: str,
    messages,
    request_id: str,
    correlation_id: str,
    start_time: float,
    event_bus,
    gateway_config: GatewayConfig,
    generation_params: dict,
    timeout_hint: float | None = None,
):
    """Generate non-streaming completion response."""
    try:
        logger.info(f"Processing inference request for model: {model_id}")

        # Emit REQUEST_QUEUED event
        await emit_request_queued_nowait(
            event_bus, model_id, request_id, messages, generation_params, stream=False
        )

        # Ensure model is loaded
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
            else:
                from src.core.resources import resource_tracker

                tracker = resource_tracker.get_resource_tracker()
                model_info = tracker.get_model_info(model_id)
                if model_info and model_info.status.name == "FAILED":
                    error_msg = model_info.error_message or "Unknown error"
                    context = {"request_id": request_id, "model_id": model_id}
                    raise ModelLoadingError(f"Model failed: {error_msg}", context)

                raise RuntimeError(f"Failed to load model {model_id}")

        # Generate completion with optional timeout hint
        completion_result = await worker_controller.generate_chat_completion(
            model_id=model_id,
            messages=messages,
            correlation_id=correlation_id,
            _request_id=request_id,  # Pass request_id for cancellation tracking
            _timeout_hint=timeout_hint,  # Pass timeout hint from upstream
            **generation_params,
        )

        # Build and return response
        response = _build_completion_response(completion_result, model_id)
        response_time_ms = (time.time() - start_time) * 1000
        api_logger.info(
            f"POST /v1/chat/completions - 200 - {response_time_ms:.2f}ms - "
            f"model:{model_id}"
        )
        return response

    except (
        ModelLoadingError,
        WorkerInitializationError,
        GatewayError,
        SyntaxErrorException,
    ) as e:
        response_time_ms = (time.time() - start_time) * 1000
        return _handle_known_error(e, request_id, response_time_ms)

    except RuntimeError as e:
        response_time_ms = (time.time() - start_time) * 1000
        return _handle_runtime_error(
            e, model_id, request_id, response_time_ms, gateway_config
        )

    except HTTPException:
        raise

    except Exception as e:
        await emit_inference_failed_nowait(event_bus, model_id, request_id, str(e))
        api_logger.error(f"Unexpected error in chat completion: {e}")
        return create_openai_error_response(
            status_code=500,
            message="Internal server error occurred",
            error_type="server_error",
            error_code="unexpected_error",
        )


def _handle_known_error(e, request_id: str, response_time_ms: float):
    """Handle ModelLoadingError, WorkerInitializationError, etc."""
    error_message = str(e)
    logger.warning(f"Gateway error: {error_message}")
    context = {"request_id": request_id}
    return create_error_response(e, 500, context)


def _handle_runtime_error(
    e: RuntimeError,
    model_id: str,
    request_id: str,
    response_time_ms: float,
    gateway_config: GatewayConfig,
):
    """Handle RuntimeError with appropriate response type."""
    error_message = str(e)

    # Timeout handling
    if "timed out" in error_message.lower() or "timeout" in error_message.lower():
        logger.warning(f"Request timeout for {model_id}: {error_message}")
        return create_openai_error_response(
            status_code=504,
            message="Request timed out",
            error_type="server_error",
            error_code=ErrorCode.REQUEST_TIMEOUT,
            request_id=request_id,
            duration_ms=response_time_ms,
        )

    # Crash error handling
    if is_crash_error(error_message):
        logger.error(f"Model crash for {model_id}: {error_message}")
        return create_model_crash_error_response(
            model_id, error_message, request_id, response_time_ms
        )

    # Connection error handling
    if is_connection_error(error_message):
        suggestion = "Try reducing max_tokens or context length"
        if (
            "connection closed by peer" in error_message.lower()
            or "transport error" in error_message.lower()
        ):
            message = "Model process crashed - likely VRAM OOM"
            error_code = ErrorCode.GPU_MEMORY_ERROR
        else:
            message = "Model process connection lost"
            error_code = ErrorCode.PROCESS_CONNECTION_LOST
        return create_openai_error_response(
            status_code=503,
            message=message,
            error_type="server_error",
            error_code=error_code,
            request_id=request_id,
            duration_ms=response_time_ms,
            suggestion=suggestion,
        )

    # Generic runtime error
    logger.error(f"Model runtime error for {model_id}: {error_message}")
    return create_openai_error_response(
        status_code=500,
        message=f"Model inference failed: {error_message}",
        error_type="server_error",
        error_code=ErrorCode.MODEL_ERROR,
        param="model",
        request_id=request_id,
        duration_ms=response_time_ms,
    )


@router.get("/chat/completions/models", tags=["OpenAI Compatible"])
async def list_chat_models(model_registry: ModelRegistry = Depends(get_model_registry)):
    """List models available for chat completions."""
    if not model_registry:
        raise HTTPException(status_code=500, detail="Model registry not initialized")

    try:
        all_models = model_registry.list_models(enabled_only=True)
        return {
            "models": [{"id": m.id, "name": m.name} for m in all_models],
            "count": len(all_models),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error listing chat models: {str(e)}"
        )
