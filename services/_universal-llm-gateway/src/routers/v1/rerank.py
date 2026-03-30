"""
Rerank endpoint — /v1/rerank

Cross-encoder reranking via Gateway-managed models.
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

from src.core.model_registry import ModelRegistry
from src.core.workers.controller import WorkerController
from src.routers.dependencies import (
    get_model_registry,
    get_worker_controller,
)
from src.schemas.rerank import RerankRequest, RerankResponse

router = APIRouter()
logger = get_logger(__name__)


async def _emit_rerank_debug(
    step: str,
    correlation_id: str,
    model_id: str,
    **extra: object,
) -> None:
    payload = {
        "step": step,
        "component": "route",
        "correlation_id": correlation_id,
        "model_id": model_id,
        **extra,
    }
    await emit_debug_event("debug.rerank.gateway", payload, source="gateway")


@router.post(
    "/rerank",
    response_model=RerankResponse,
    tags=["Reranking"],
)
async def create_rerank(
    request: Request,
    rerank_request: RerankRequest,
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller: WorkerController = Depends(get_worker_controller),
) -> RerankResponse:
    """Score (query, passage) pairs via cross-encoder reranking."""
    start_time = time.time()

    correlation_id = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())

    model_id = rerank_request.model
    query = rerank_request.query
    passages = rerank_request.passages

    if not query:
        raise HTTPException(status_code=400, detail="Empty query")
    if not passages:
        raise HTTPException(status_code=400, detail="No passages provided")

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
        "Rerank request: model=%s, passages=%d, correlation_id=%s",
        model_id,
        len(passages),
        correlation_id,
    )
    await _emit_rerank_debug(
        "request_received",
        correlation_id,
        model_id,
        passage_count=len(passages),
    )

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

    rerank_started_at = time.monotonic()
    try:
        result = await worker_controller.rerank(
            model_id=model_id,
            query=query,
            passages=passages,
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
        elapsed_ms = round((time.monotonic() - rerank_started_at) * 1000, 1)
        logger.error(
            "Rerank failed (retryable=%s): %s",
            is_transient,
            e,
            extra={"correlation_id": correlation_id},
        )
        await _emit_rerank_debug(
            "rerank_error",
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
                message=f"Rerank failed: {e}",
                source="gateway",
                retryable=is_transient,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        ) from e
    except Exception as e:
        elapsed_ms = round((time.monotonic() - rerank_started_at) * 1000, 1)
        logger.error(
            "Unexpected rerank error: %s",
            e,
            extra={"correlation_id": correlation_id},
        )
        await _emit_rerank_debug(
            "rerank_error",
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
                message="Internal rerank error",
                source="gateway",
                retryable=False,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        ) from e

    scores = result.get("scores", [])
    if len(scores) != len(passages):
        logger.error(
            "Rerank score count mismatch: expected %d, got %d",
            len(passages),
            len(scores),
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Score count mismatch from worker",
                source="gateway",
                retryable=False,
                data={"model": model_id, "correlation_id": correlation_id},
            ),
        )

    response = RerankResponse(scores=scores, model=model_id)

    elapsed = (time.time() - start_time) * 1000
    logger.info(
        "Rerank completed: %d scores in %.1fms",
        len(scores),
        elapsed,
        extra={"correlation_id": correlation_id},
    )
    await _emit_rerank_debug(
        "response_ready",
        correlation_id,
        model_id,
        elapsed_ms=round((time.monotonic() - rerank_started_at) * 1000, 1),
        score_count=len(scores),
    )

    return response
