"""
Request body extraction utilities.

Extracts raw JSON request body from multiple sources with fallback chain:
1. Raw body bytes from RawBodyCacheMiddleware (preferred - uncorrupted)
2. request.json() (may be corrupted by FastAPI caching)
3. Pydantic __pydantic_extra__ (partial recovery)
4. model_dump() (last resort - corrupted)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

    from src.schemas.chat_completion import ChatCompletionRequest

from ..errors import RequestErrorBuilder

logger = get_logger(__name__)


async def extract_original_request(
    request: Request,
    chat_request: ChatCompletionRequest,
) -> dict[str, Any]:
    """
    Extract original request from multiple sources.

    CRITICAL: We need the RAW JSON bytes BEFORE FastAPI/Pydantic corrupts it.
    RawBodyCacheMiddleware captures request.state.raw_body_bytes before processing.

    Fallback chain:
    1. Raw body bytes from middleware (BEFORE FastAPI/Pydantic processing)
    2. request.json() (may be corrupted by FastAPI caching)
    3. Pydantic __pydantic_extra__ (for extra="allow" fields)
    4. model_dump() (last resort - will be corrupted)

    Args:
        request: FastAPI Request object
        chat_request: Parsed ChatCompletionRequest

    Returns:
        Original request as dict

    Raises:
        HTTPException: If all extraction methods fail
    """
    # Method 1: Try raw body bytes from middleware (BEFORE FastAPI/Pydantic processing)
    result = _try_raw_body_bytes(request)
    if result is not None:
        return result

    # Method 2: Try request.json() (may be corrupted by FastAPI caching)
    result = await _try_request_json(request)
    if result is not None:
        return result

    # Method 3: Fallback to Pydantic __pydantic_extra__ (for extra="allow" fields)
    result = _try_pydantic_extra(chat_request)
    if result is not None:
        return result

    # Method 4: Last resort - model_dump (will be corrupted)
    return _fallback_model_dump(chat_request)


def _try_raw_body_bytes(request: Request) -> dict[str, Any] | None:
    """Try to extract from raw body bytes cached by middleware."""
    try:
        if hasattr(request.state, "raw_body_bytes"):
            raw_body_dict = json.loads(request.state.raw_body_bytes.decode("utf-8"))
            if isinstance(raw_body_dict, dict):
                logger.debug(
                    "✅ Using RAW body bytes (pre-Pydantic) "
                    f"with {len(raw_body_dict)} fields"
                )
                return raw_body_dict
    except Exception as e:
        logger.debug(f"Could not read raw body bytes from middleware: {e}")
    return None


async def _try_request_json(request: Request) -> dict[str, Any] | None:
    """Try to extract from request.json() (may be corrupted)."""
    try:
        raw_request_body = await request.json()
        if isinstance(raw_request_body, dict):
            logger.debug(
                "⚠️ Using request.json() (may be corrupted) "
                f"with {len(raw_request_body)} fields"
            )
            return raw_request_body.copy()
    except Exception as e:
        logger.debug(f"Could not read request.json(): {e}")
    return None


def _try_pydantic_extra(chat_request: ChatCompletionRequest) -> dict[str, Any] | None:
    """Try to extract from Pydantic __pydantic_extra__ (partial recovery)."""
    try:
        if (
            hasattr(chat_request, "__pydantic_extra__")
            and chat_request.__pydantic_extra__ is not None
        ):
            # Get base fields from model
            original_request = {}
            if chat_request.model:
                original_request["model"] = chat_request.model
            # Use 'is not None' to preserve empty lists (messages=[])
            if chat_request.messages is not None:
                original_request["messages"] = [
                    {"role": msg.role, "content": msg.content}
                    for msg in chat_request.messages
                ]
            elif chat_request.prompt is not None:
                original_request["prompt"] = chat_request.prompt

            # Add extra fields (uncorrupted since Pydantic didn't validate them)
            original_request.update(chat_request.__pydantic_extra__)
            logger.debug(
                "❌ Using __pydantic_extra__ fallback "
                f"with {len(original_request)} fields"
            )
            return original_request
    except Exception as e:
        logger.debug(f"Could not use __pydantic_extra__: {e}")
    return None


def _fallback_model_dump(chat_request: ChatCompletionRequest) -> dict[str, Any]:
    """Last resort: use model_dump (data will be corrupted)."""
    try:
        original_request = chat_request.model_dump(mode="python", exclude_unset=True)
        logger.warning(
            "❌❌ Using model_dump (DATA IS CORRUPTED) "
            f"with {len(original_request)} fields"
        )
        return original_request
    except Exception as e:
        logger.error(f"Failed to extract request: {e}")
        raise RequestErrorBuilder.invalid_request("Invalid request format")
