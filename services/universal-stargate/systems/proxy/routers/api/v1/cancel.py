"""
Pipeline cancellation endpoint.

Cancels federation requests by request_id.
Used by pipeline layer when steps timeout.

Cancellation path:
    Remote (via MasterRequestTracker) - for requests already forwarded.
    Queue-based cancellation was removed with unified capacity tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from universal_logging import get_logger
from universal_protocol import ErrorCode, error_envelope

if TYPE_CHECKING:
    from systems.proxy.stargate.proxy import StargateProxy

logger = get_logger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class CancelRequest(BaseModel):
    """Request body for cancellation."""

    request_id: str
    model_id: str | None = None


class CancelResponse(BaseModel):
    """Response for cancellation."""

    cancelled: bool
    request_id: str


# Injected at app startup
_proxy: StargateProxy | None = None


def set_proxy(proxy: StargateProxy) -> None:
    """Inject proxy for cancellation API."""
    global _proxy
    _proxy = proxy


@router.post("/cancel", response_model=CancelResponse)
async def cancel_request(body: CancelRequest) -> CancelResponse:
    """
    Cancel a federation request by request_id.

    Called by pipeline layer when step times out.
    Attempts cancellation from:
    1. Queues (if request is still waiting)
    2. Remote federation (if request was already forwarded)
    """
    if _proxy is None:
        raise HTTPException(
            status_code=503,
            detail=error_envelope(
                code=ErrorCode.RESOURCE_UNAVAILABLE,
                message="Proxy not initialized",
                source="master",
                retryable=False,
            ),
        )

    request_id = body.request_id
    model_id = body.model_id

    try:
        # First try to cancel from queues (if still waiting)
        queue_cancelled = _proxy.cancel_request(request_id, model_id)

        # Then try to cancel from remote (if already forwarded)
        remote_cancelled = False
        federation = _proxy.federation_integration
        if federation and federation.request_tracker:
            remote_cancelled = await federation.request_tracker.cancel(request_id)

        cancelled = queue_cancelled or remote_cancelled

        if cancelled:
            where = []
            if queue_cancelled:
                where.append("queue")
            if remote_cancelled:
                where.append("remote")
            logger.info(
                "Cancelled request %s from %s", request_id[:8], ", ".join(where)
            )
        else:
            logger.debug(
                "Request %s not found in queues or remote (may already be terminal)",
                request_id[:8],
            )

        return CancelResponse(
            cancelled=cancelled,
            request_id=request_id,
        )

    except Exception as e:
        logger.error("Cancel failed for %s: %s", request_id[:8], e)
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=f"Cancel failed: {e}",
                source="master",
                retryable=False,
            ),
        ) from e
