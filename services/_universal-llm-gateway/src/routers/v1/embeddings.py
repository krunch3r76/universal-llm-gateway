"""
Embeddings endpoint - /v1/embeddings

OpenAI-compatible text embedding generation.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import (
    get_model_registry,
    get_worker_controller,
)
from src.schemas.embedding import (
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUsage,
)

router = APIRouter()
logger = get_logger(__name__)


async def _emit_embedding_debug(
    step: str,
    correlation_id: str,
    model_id: str,
    **extra: object,
) -> None:
    """Emit a temporary debug event for end-to-end embedding tracing."""
    payload = {
        "step": step,
        "component": "route",
        "correlation_id": correlation_id,
        "model_id": model_id,
        **extra,
    }
    await emit_debug_event("debug.embedding.gateway", payload, source="gateway")


@router.post(
    "/embeddings",
    response_model=EmbeddingResponse,
    tags=["OpenAI Compatible"],
)
async def create_embeddings(
    request: Request,  # Added: for correlation ID
    embedding_request: EmbeddingRequest,
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
):
    """Generate embeddings for input text(s)."""
    start_time = time.time()

    # Extract or generate correlation ID
    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())

    # Normalize input to list
    input_texts = embedding_request.input
    if isinstance(input_texts, str):
        input_texts = [input_texts]

    if not input_texts:
        raise HTTPException(status_code=400, detail="No input provided")

    model_id = embedding_request.model

    # Validate model exists
    if not model_registry.get_model_config(model_id):
        raise HTTPException(
            status_code=404,
            detail=error_envelope(
                code=ErrorCode.MODEL_NOT_FOUND,
                message=f"Model '{model_id}' not found",
                source="gateway",
                retryable=False,
                data={"model": model_id},
            ),
        )

    logger.info(
        f"Embedding request: model={model_id}, inputs={len(input_texts)}, "
        f"correlation_id={correlation_id}"
    )
    await _emit_embedding_debug(
        "request_received",
        correlation_id,
        model_id,
        input_count=len(input_texts),
    )

    # Ensure model is loaded
    if not await worker_controller.ensure_model_loaded(
        model_id, correlation_id=correlation_id
    ):
        raise HTTPException(
            status_code=503,
            detail=error_envelope(
                code=ErrorCode.RESOURCE_UNAVAILABLE,
                message=f"Failed to load model '{model_id}'",
                source="gateway",
                retryable=True,
                data={"model": model_id},
            ),
        )

    # Generate embeddings with correlation ID
    generate_started_at = time.monotonic()
    try:
        result = await worker_controller.generate_embeddings(
            model_id=model_id,
            input_texts=input_texts,
            correlation_id=correlation_id,
        )
    except RuntimeError as e:
        error_str = str(e).lower()
        is_transient = "not loaded" in error_str or "not ready" in error_str
        status = 503 if is_transient else 500
        code = (
            ErrorCode.RESOURCE_UNAVAILABLE
            if is_transient
            else ErrorCode.UNEXPECTED_ERROR
        )
        elapsed_ms = round((time.monotonic() - generate_started_at) * 1000, 1)
        logger.error(
            f"Embedding generation failed (retryable={is_transient}): {e}",
            extra={"correlation_id": correlation_id},
        )
        await _emit_embedding_debug(
            "generate_error",
            correlation_id,
            model_id,
            elapsed_ms=elapsed_ms,
            retryable=is_transient,
            error_type=type(e).__name__,
            error=str(e),
        )
        raise HTTPException(
            status_code=status,
            detail=error_envelope(
                code=code,
                message=f"Embedding generation failed: {e}",
                source="gateway",
                retryable=is_transient,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        ) from e
    except Exception as e:
        elapsed_ms = round((time.monotonic() - generate_started_at) * 1000, 1)
        logger.error(
            f"Unexpected embedding error: {e}",
            extra={"correlation_id": correlation_id},
        )
        await _emit_embedding_debug(
            "generate_error",
            correlation_id,
            model_id,
            elapsed_ms=elapsed_ms,
            error_type=type(e).__name__,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Internal embedding error",
                source="gateway",
                retryable=False,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        ) from e

    # Build response - validate result shape
    data_items = result.get("data")
    if not isinstance(data_items, list):
        logger.error(
            "Invalid embedding response shape: missing 'data' list",
            extra={
                "correlation_id": correlation_id,
                "result_keys": list(result.keys()),
            },
        )
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Invalid embedding response from worker",
                source="gateway",
                retryable=False,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        )

    data = [
        EmbeddingData(
            embedding=item["embedding"],
            index=item["index"],
        )
        for item in data_items
    ]

    usage_info = result.get("usage", {})
    usage = EmbeddingUsage(
        prompt_tokens=usage_info.get("prompt_tokens", 0),
        total_tokens=usage_info.get("total_tokens", 0),
    )

    response = EmbeddingResponse(
        data=data,
        model=model_id,
        usage=usage,
    )

    elapsed = (time.time() - start_time) * 1000
    logger.info(
        f"Embeddings generated: {len(data)} vectors in {elapsed:.1f}ms",
        extra={"correlation_id": correlation_id},
    )
    await _emit_embedding_debug(
        "response_ready",
        correlation_id,
        model_id,
        elapsed_ms=round((time.monotonic() - generate_started_at) * 1000, 1),
        output_count=len(data),
    )

    return response
