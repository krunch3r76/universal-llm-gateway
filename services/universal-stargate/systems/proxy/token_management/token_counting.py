"""
Token counting HTTP request wrapper.
Handles communication with gateway token counting endpoint.
"""

import asyncio
import json
from dataclasses import dataclass

import httpx

from src.schemas.tokens import TokenCountRequest


@dataclass
class TokenCountResult:
    """Result from token counting request"""

    success: bool
    input_tokens: int | None = None
    context_limit: int | None = None
    raw_generation_space: int | None = None
    status_code: int | None = None
    error_message: str | None = None
    elapsed_seconds: float | None = None


async def count_tokens(
    http_client: httpx.AsyncClient,
    endpoint_url: str,
    request: TokenCountRequest,
    timeout_seconds: float,
) -> TokenCountResult:
    """
    Count tokens using gateway endpoint.

    This is a pure HTTP wrapper - no logging, caller handles all logging.

    Args:
        http_client: HTTP client to use for request
        endpoint_url: Token counting endpoint URL
        request: Token count request object
        timeout_seconds: Request timeout

    Returns:
        TokenCountResult with success/failure and data
    """
    start_time = asyncio.get_event_loop().time()

    try:
        # Safety check: Ensure HTTP client is active
        if hasattr(http_client, "is_closed") and http_client.is_closed:
            elapsed = asyncio.get_event_loop().time() - start_time
            return TokenCountResult(
                success=False,
                error_message="http_client_closed",
                elapsed_seconds=elapsed,
            )

        # Execute token counting request
        headers = {"Content-Type": "application/json"}
        response = await http_client.post(
            endpoint_url,
            headers=headers,
            content=json.dumps(request.model_dump(exclude_unset=True)).encode("utf-8"),
            timeout=timeout_seconds,
        )

        elapsed = asyncio.get_event_loop().time() - start_time

        if response.status_code == 200:
            # Success - parse response
            token_data = response.json()
            return TokenCountResult(
                success=True,
                input_tokens=token_data["token_count"],
                context_limit=token_data["context_limit"],
                raw_generation_space=token_data["max_generation_tokens"],
                status_code=200,
                elapsed_seconds=elapsed,
            )
        else:
            # Non-200 response
            return TokenCountResult(
                success=False,
                status_code=response.status_code,
                error_message=response.text,
                elapsed_seconds=elapsed,
            )

    except TimeoutError:
        elapsed = asyncio.get_event_loop().time() - start_time
        return TokenCountResult(
            success=False, error_message="timeout", elapsed_seconds=elapsed
        )

    except Exception as e:
        elapsed = asyncio.get_event_loop().time() - start_time
        return TokenCountResult(
            success=False, error_message=str(e), elapsed_seconds=elapsed
        )
