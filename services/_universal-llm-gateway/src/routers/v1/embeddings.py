"""
Embeddings endpoint - /v1/embeddings

OpenAI-compatible text embedding generation.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
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

    # Ensure model is loaded
    if not await worker_controller.ensure_model_loaded(model_id):
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
    try:
        result = await worker_controller.generate_embeddings(
            model_id=model_id,
            input_texts=input_texts,
            correlation_id=correlation_id,
        )
    except RuntimeError as e:
        logger.error(
            f"Embedding generation failed: {e}",
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=f"Embedding generation failed: {e}",
                source="gateway",
                retryable=False,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        )
    except Exception as e:
        logger.error(
            f"Unexpected embedding error: {e}",
            extra={"correlation_id": correlation_id},
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
        )

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

    return response
