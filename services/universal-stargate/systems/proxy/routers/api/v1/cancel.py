"""
Pipeline cancellation endpoint.

Cancels federation requests by request_id or cancel_group.
Used by pipeline layer when steps timeout.

Cancellation path:
    Queue (via proxy.cancel_request) - for requests still waiting.
    Remote (via MasterRequestTracker) - for requests already forwarded.
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
    """Request body for cancellation.

    Exactly one of request_id or cancel_group must be provided.
    cancel_group cancels all requests registered under that group ID.
    """

    request_id: str | None = None
    cancel_group: str | None = None
    model_id: str | None = None


class CancelResponse(BaseModel):
    """Response for cancellation."""

    cancelled: bool
    request_id: str  # request_id or cancel_group that was processed
    cancelled_count: int = 0  # Number of requests cancelled (for group cancel)


# Injected at app startup
_proxy: StargateProxy | None = None


def set_proxy(proxy: StargateProxy) -> None:
    """Inject proxy for cancellation API."""
    global _proxy
    _proxy = proxy


@router.post("/cancel", response_model=CancelResponse)
async def cancel_request(body: CancelRequest) -> CancelResponse:
    """
    Cancel a federation request by request_id or cancel_group.

    Called by pipeline layer when step times out.
    Attempts cancellation from:
    1. Queues (if request is still waiting)
    2. Remote federation (if request was already forwarded)
    """
    proxy = _proxy
    if proxy is None:
        raise HTTPException(
            status_code=503,
            detail=error_envelope(
                code=ErrorCode.RESOURCE_UNAVAILABLE,
                message="Proxy not initialized",
                source="master",
                retryable=False,
            ),
        )

    has_request_id = bool(body.request_id)
    has_cancel_group = bool(body.cancel_group)
    if has_request_id == has_cancel_group:
        raise HTTPException(
            status_code=400,
            detail=error_envelope(
                code=ErrorCode.INVALID_REQUEST,
                message="Exactly one of request_id or cancel_group must be provided",
                source="master",
                retryable=False,
            ),
        )

    try:
        if body.cancel_group:
            federation = proxy.federation_integration
            count = 0
            if federation and federation.request_tracker:
                count = await federation.request_tracker.cancel_group(body.cancel_group)
            cancelled = count > 0
            if cancelled:
                logger.info(
                    "Cancelled %d request(s) in group %s",
                    count,
                    body.cancel_group[:8],
                )
            return CancelResponse(
                cancelled=cancelled,
                request_id=body.cancel_group,
                cancelled_count=count,
            )

        request_id = body.request_id
        model_id = body.model_id
        if request_id is None:
            raise HTTPException(
                status_code=400,
                detail=error_envelope(
                    code=ErrorCode.INVALID_REQUEST,
                    message="request_id is required when cancel_group is not provided",
                    source="master",
                    retryable=False,
                ),
            )

        # First try to cancel from queues (if still waiting)
        queue_cancelled = proxy.cancel_request(request_id, model_id)

        # Then try to cancel from remote (if already forwarded)
        remote_cancelled = False
        federation = proxy.federation_integration
        if federation and federation.request_tracker:
            remote_cancelled = await federation.request_tracker.cancel(request_id)

        cancelled = queue_cancelled or remote_cancelled

        if cancelled:
            where: list[str] = []
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

    except HTTPException:
        raise
    except Exception as e:
        label = (body.cancel_group or body.request_id or "unknown")[:8]
        logger.error("Cancel failed for %s: %s", label, e)
        raise HTTPException(
            status_code=500,
            detail=error_envelope(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Cancel failed",
                source="master",
                retryable=False,
            ),
        ) from e
