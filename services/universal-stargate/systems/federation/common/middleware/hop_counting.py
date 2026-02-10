"""
Loop prevention middleware via hop counting.

INVARIANT: ∀ request r: hop_count(r) ≤ max_hops(r)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from universal_logging import get_logger

from ..config import FederationConfig
from ..types import HEADER_FEDERATION_HOP_COUNT

logger = get_logger(__name__)


class HopCountMiddleware(BaseHTTPMiddleware):
    """
    Middleware that tracks and validates hop count.

    INVARIANT:
      hop_count(r) ≤ max_hops
      ∧ forward(r) ⟹ hop_count(r') = hop_count(r) + 1
    """

    def __init__(self, app, config: FederationConfig):
        super().__init__(app)
        self._max_hops = config.max_hops

    async def dispatch(self, request: Request, call_next):
        # Get hop count from header
        hop_count_str = request.headers.get(HEADER_FEDERATION_HOP_COUNT, "0")

        try:
            hop_count = int(hop_count_str)
        except ValueError:
            return JSONResponse(
                status_code=400, content={"error": "Invalid hop count header"}
            )

        # Validate hop limit
        if hop_count >= self._max_hops:
            logger.warning(f"Hop limit exceeded: {hop_count} >= {self._max_hops}")
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"Hop limit exceeded: {hop_count} >= {self._max_hops}"
                },
            )

        # Store incremented hop count for forwarding
        request.state.federation_hop_count = hop_count + 1

        return await call_next(request)
