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

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import (
    get_event_bus,
    get_model_registry,
    get_worker_controller,
)
from src.schemas.chat_completion import ChatCompletionRequest, ChatCompletionResponse

from .model_resolution import resolve_model_id
from .non_streaming import generate_non_streaming_response
from .response_build import build_generation_params, resolve_gateway_url
from .stream import generate_streaming_response

router = APIRouter()
logger = get_logger(__name__)


def _extract_internal_request_id(request: Request) -> str | None:
    """Prefer Stargate-provided identity so request-scoped telemetry correlates across hops."""
    header_value = request.headers.get("X-Internal-Request-ID")
    if header_value is None:
        return None
    stripped = header_value.strip()
    return stripped or None


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
    event_bus=Depends(get_event_bus),
):
    """OpenAI-compatible chat completion endpoint."""
    start_time = time.time()
    request_id = _extract_internal_request_id(request) or str(uuid.uuid4())
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
    generation_params = build_generation_params(completion_request)
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
        gateway_url = resolve_gateway_url(request)
        return StreamingResponse(
            generate_streaming_response(
                worker_controller=worker_controller,
                model_id=model_id_str,
                messages=messages_or_prompt,
                event_bus=event_bus,
                correlation_id=correlation_id,
                timeout_hint=timeout_hint,
                request_id=request_id,
                gateway_url=gateway_url,
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
        return await generate_non_streaming_response(
            worker_controller=worker_controller,
            model_id=model_id_str,
            messages=messages_or_prompt,
            request_id=request_id,
            correlation_id=correlation_id,
            start_time=start_time,
            event_bus=event_bus,
            generation_params=generation_params,
            timeout_hint=timeout_hint,
            request=request,
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
