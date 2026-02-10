"""Response formatting for Flux image generation."""

import base64
import io
import time

from universal_logging import get_logger

logger = get_logger(__name__)


def format_image_response(image, response_format: str) -> dict:
    """
    Convert PIL Image to API response format.

    Args:
        image: PIL Image object
        response_format: "url" or "b64_json"

    Returns:
        Response dictionary with created timestamp and data

    Raises:
        Exception: If image conversion fails
    """
    # Convert to bytes
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_bytes = buffer.getvalue()

    # Format as base64 data URL
    # (Both "url" and "b64_json" use data URLs since we don't have storage service)
    image_b64 = base64.b64encode(image_bytes).decode()
    url = f"data:image/png;base64,{image_b64}"

    logger.info(f"Image formatted successfully: {len(image_bytes)} bytes")

    return {
        "created": int(time.time()),
        "data": [{"url": url}],
    }
