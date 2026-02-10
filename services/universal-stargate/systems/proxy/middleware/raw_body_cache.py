"""Middleware to cache raw request body before FastAPI/Pydantic processes it.

CRITICAL: FastAPI's request.json() returns cached data that may be corrupted by Pydantic parsing.
This middleware captures the raw bytes BEFORE any processing to preserve client data exactly.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RawBodyCacheMiddleware(BaseHTTPMiddleware):
    """Cache raw request body before FastAPI/Pydantic processing."""

    async def dispatch(self, request: Request, call_next):
        """Capture raw body bytes and store in request.state before processing."""
        # Only cache for POST/PUT/PATCH requests
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                # Read body bytes (this consumes the stream)
                body_bytes = await request.body()

                # Store raw bytes in request.state for later retrieval
                request.state.raw_body_bytes = body_bytes

                # FastAPI will re-read from the cached _body attribute
                # We set it here so FastAPI can still process it normally
                request._body = body_bytes  # noqa: SLF001
            except Exception:
                # If reading fails, continue without caching
                pass

        response = await call_next(request)
        return response
