"""Chat completions endpoint"""

import traceback

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from universal_logging import get_logger

from src.schemas.chat_completion import ChatCompletionRequest

from ...core.errors import RequestErrorBuilder
from ...dependencies import get_auth_dependency, get_proxy
from ...stargate_core import StargateProxy

logger = get_logger(__name__)
router = APIRouter(tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    chat_request: ChatCompletionRequest = Body(...),
    model: str | None = Query(
        None, description="Model to use (overrides request body)"
    ),
    profile: str | None = Query(
        None,
        description="Profile/filter to apply (one-shot, not persisted)",
        alias="filter",
    ),
    disable_profile: bool | None = Query(
        False, description="Disable profile application"
    ),
    skip_token_counting: bool | None = Query(
        None, description="Skip token counting for time-critical requests"
    ),
    pseudostream: bool = Query(
        False,
        description=(
            "Local models only: force upstream SSE generation, accumulate on "
            "master, return one JSON chat.completion (X-ULG-Pseudostream). "
            "Conflicts with body stream=true."
        ),
    ),
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """
    Submit a chat completion request.

    Flow:
    1. Request preparation (validation, transformation)
    2. Submission to queue (or immediate execution)
    3. Async wait for result
    4. Return response
    """
    try:
        logger.info("📨 ENDPOINT: Calling proxy.process_chat_completion")
        response = await proxy.process_chat_completion(
            request,
            chat_request,
            model,
            profile,
            disable_profile,
            skip_token_counting,
            pseudostream=pseudostream,
        )
        logger.info(f"✅ ENDPOINT: Got response from proxy: {type(response)}")
        logger.info("🔍 ENDPOINT: Returning response to client")
        return response
    except HTTPException as e:
        # Log the final error that will be returned to client
        logger.error(
            "❌ ENDPOINT: HTTPException caught - status: %s, detail: %s",
            e.status_code,
            e.detail,
        )
        logger.error(f"❌ ENDPOINT: HTTPException type: {type(e)}")
        logger.error("❌ ENDPOINT: Re-raising HTTPException to let FastAPI handle it")
        # Let FastAPI handle HTTPException automatically instead of manual conversion
        raise
    except Exception as e:
        logger.error(f"❌ ENDPOINT: Caught unexpected exception: {e}")
        logger.error(f"🔍 ENDPOINT: Full traceback:\n{traceback.format_exc()}")
        logger.error("🔍 ENDPOINT: Converting to HTTPException 500")
        raise RequestErrorBuilder.internal_error(str(e), operation="chat_completions")
