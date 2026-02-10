"""Gateway HTTP proxy for image generation requests."""

import httpx
from fastapi import HTTPException
from universal_logging import get_logger

logger = get_logger(__name__)

# Default timeout for image generation (matches gateway.request_timeout)
IMAGE_GENERATION_TIMEOUT = 1800.0  # 30 minutes


async def forward_image_request(
    gateway_url: str,
    request_data: dict,
    headers: dict[str, str] | None = None,
) -> dict:
    """
    Forward image generation request to Gateway via HTTP.

    Args:
        gateway_url: Gateway base URL (e.g., http://localhost:9998)
        request_data: Request payload to forward
        headers: Optional headers to include

    Returns:
        Response from Gateway

    Raises:
        HTTPException: If forwarding fails
    """
    url = f"{gateway_url}/v1/images/generations"

    # Clean headers
    clean_headers = {"content-type": "application/json"}
    if headers:
        clean_headers.update(
            {
                k.lower(): v
                for k, v in headers.items()
                if k.lower() not in ("host", "content-length")
            }
        )

    logger.debug(f"Forwarding image request to {url}")

    # TODO: Future SSE progress streaming hook point
    # For Phase 3, this could be converted to SSE for real-time progress

    try:
        async with httpx.AsyncClient(timeout=IMAGE_GENERATION_TIMEOUT) as client:
            response = await client.post(url, json=request_data, headers=clean_headers)

            if response.status_code >= 400:
                logger.error(f"Gateway error: {response.status_code} - {response.text}")
                content_type = response.headers.get("content-type", "")
                detail = (
                    response.json()
                    if content_type.startswith("application/json")
                    else response.text
                )
                raise HTTPException(status_code=response.status_code, detail=detail)

            return response.json()

    except httpx.TimeoutException:
        logger.error(f"Gateway timeout after {IMAGE_GENERATION_TIMEOUT}s")
        raise HTTPException(status_code=504, detail="Image generation timeout")
    except httpx.ConnectError as e:
        logger.error(f"Gateway connection failed: {e}")
        raise HTTPException(status_code=503, detail="Gateway unavailable")
