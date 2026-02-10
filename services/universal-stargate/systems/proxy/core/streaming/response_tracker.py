"""
Tracks StreamingResponse lifecycle to detect unconsumed responses.

This helps diagnose cases where streaming responses are created but never consumed by clients.
"""

import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from universal_logging import get_logger

logger = get_logger(__name__)


class TrackedStreamingResponse(StreamingResponse):
    """
    A StreamingResponse that tracks whether it was consumed.

    Logs warnings if the response is garbage collected without being consumed.
    """

    def __init__(
        self,
        content: AsyncIterator[Any],
        status_code: int = 200,
        headers: dict | None = None,
        media_type: str | None = None,
        background: Any | None = None,
        request_id: str | None = None,
        model: str | None = None,
    ):
        self.request_id = request_id or "unknown"
        self.model = model or "unknown"
        self.created_at = time.time()
        self.consumed = False
        self.first_chunk_at = None

        # Wrap the content iterator to track consumption
        async def tracked_content():
            self.consumed = True
            self.first_chunk_at = time.time()
            async for chunk in content:
                yield chunk

        super().__init__(
            content=tracked_content(),
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )

    def __del__(self):
        """Log warning if response was never consumed"""
        if not self.consumed:
            age = time.time() - self.created_at
            logger.warning(
                f"⚠️ STREAMING RESPONSE NEVER CONSUMED - request_id: {self.request_id}, "
                f"model: {self.model}, age: {age:.2f}s"
            )
