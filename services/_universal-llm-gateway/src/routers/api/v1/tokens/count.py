"""POST /api/v1/tokens/count - Token counting endpoint"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_registry, get_worker_controller
from src.schemas.tokens import TokenCountRequest, TokenCountResponse

router = APIRouter(prefix="/v1/tokens", tags=["Token Management"])
logger = get_logger(__name__)


@router.post("/count", response_model=TokenCountResponse)
async def count_tokens(
    request: TokenCountRequest,
    model_registry: ModelRegistry = Depends(get_model_registry),
    worker_controller=Depends(get_worker_controller),
):
    """
    Count tokens in messages or prompt using IPC-based worker processes.

    This endpoint triggers model loading/switching if needed, then provides
    exact token counts that match what the model will process during inference.

    **Supported Input Formats:**
    - Message lists with typed content (text-only or multi-modal with images)
    - Prompt strings for direct tokenization

    **Supported Model Types:**
    - GGUF: Lightweight tokenizer with vision model support
    - AWQ/GPTQ: Tokenizer-only loading (no full model required)
    - API Proxy: Uses tiktoken for OpenAI models

    **Features:**
    - Automatic model loading and switching
    - Multi-modal content support (images + text for vision models)
    - Exact tokenization only - no estimation fallbacks
    - Consistent with inference behavior
    - Minimal memory usage for token counting operations
    - Schema-validated input with discriminated unions
    """
    if not model_registry:
        raise HTTPException(status_code=500, detail="Model registry not initialized")

    if not worker_controller:
        raise HTTPException(status_code=500, detail="Worker controller not initialized")

    # Gateway uses canonical model IDs only (no `:N` instance suffix)
    # Stargate rejects `:N` at the API boundary
    model_id = request.model_name

    # Get model info using model_registry.get_model_info
    model_info = model_registry.get_model_info(model_id)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    resolved_openai_api_model_id = model_info.id

    try:
        # Get context length from model registry
        # (using requested context length if provided)
        context_length = model_registry.get_model_max_tokens(
            resolved_openai_api_model_id, request.requested_context_length
        )
        if not context_length:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Context length not available for model "
                    f"'{resolved_openai_api_model_id}'"
                ),
            )

        # Compute effective per-slot context: with parallel_slots > 1 the
        # engine splits its KV cache across slots, so each slot only has
        # context_length // parallel_slots usable tokens.
        loader_config = model_registry.get_model_loader_config(
            resolved_openai_api_model_id
        )
        parallel_slots = 1
        if loader_config is not None:
            parallel_slots = max(1, loader_config.get("parallel_slots", 1))
        effective_context = max(1, context_length // parallel_slots)
        if parallel_slots > 1:
            logger.info(
                f"Slot-aware context for {model_id}: "
                f"{context_length} total / {parallel_slots} slots "
                f"= {effective_context} effective per slot"
            )

        # Check ResourceTracker state before attempting token counting
        # Only ERROR state should cause immediate failure - BUSY means queue/wait
        from src.core.resources import resource_tracker

        tracker_info = resource_tracker.get_model_info(model_id)
        if tracker_info:
            current_status = tracker_info.status.value
            if current_status == "error":
                # Model is in error state - provide clear error message
                error_msg = tracker_info.error_message or "Model is in error state"
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": {
                            "message": (
                                f"Model '{model_id}' is in error state: {error_msg}"
                            ),
                            "type": "model_error",
                            "code": "model_error_state",
                            "model": model_id,
                            "error_details": error_msg,
                        }
                    },
                )
            # Note: BUSY state is normal - model is processing another request
            # The ensure_model_loaded() call below will handle waiting/queueing

        # Ensure model is loaded and ready for token counting (triggers model switching)
        # Note: In federation scenarios, model may already be loaded by Remote Stargate
        logger.info(f"Ensuring model {model_id} is loaded")
        try:
            if not await worker_controller.ensure_model_loaded(model_id):
                # Check if model is already loaded (federation scenario)
                if tracker_info and tracker_info.status.value == "loaded":
                    logger.info(f"Model {model_id} already loaded (federation path)")
                else:
                    raise RuntimeError(
                        f"Failed to load model {model_id} for token counting"
                    )
        except Exception as e:
            # If model is already loaded, proceed with token counting
            if tracker_info and tracker_info.status.value == "loaded":
                logger.info(
                    f"Model {model_id} already loaded, proceeding with token counting (error: {e})"
                )
            else:
                raise

        # Count tokens using IPC-based worker processes
        try:
            # Convert messages to dict format if needed
            if request.messages:
                message_or_prompt = [
                    msg.model_dump(exclude_unset=True) for msg in request.messages
                ]
            else:
                message_or_prompt = request.prompt

            # Use the model manager's count_tokens method (IPC-based)
            result = await worker_controller.count_tokens(
                model_id,
                message_or_prompt,
                use_cpu=False,  # Always False - kept for API compatibility
                context_length=context_length,
            )

            # Extract token count from the result
            if isinstance(result, dict) and "token_count" in result:
                token_count = result["token_count"]
            else:
                token_count = result

            # Log the result for debugging
            logger.info(f"🔍 Token counting result for {model_id}: {result}")
            logger.info(f"🔍 Extracted token count: {token_count}")

            # Calculate available tokens for generation using effective
            # per-slot context (accounts for KV cache split across slots)
            max_generation_tokens = max(0, effective_context - token_count)

            return TokenCountResponse(
                token_count=token_count,
                context_limit=effective_context,
                max_generation_tokens=max_generation_tokens,
                token_counting_enabled=True,
            )

        except Exception as e:
            logger.error(f"IPC token counting failed for model {model_id}: {e}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "message": f"IPC tokenization failed: {str(e)}",
                        "type": "server_error",
                        "code": "tokenization_error",
                    }
                },
            )

    except Exception as e:
        logger.error(f"Error in token counting endpoint: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "message": f"Internal server error: {str(e)}",
                    "type": "server_error",
                    "code": "internal_error",
                }
            },
        )
