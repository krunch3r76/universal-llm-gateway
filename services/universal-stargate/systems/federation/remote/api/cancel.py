"""
Federation cancel endpoint.

Cancels forwarded requests using shared ActiveRequestStore.

INVARIANT: requires_federation_auth
"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from .request_store import ActiveRequestStore

logger = get_logger(__name__)


class CancelStatus(StrEnum):
    """Cancel operation status."""

    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass
class CancelResult:
    """Result of cancel operation."""

    status: CancelStatus
    request_id: str


def try_cancel_request(
    request_store: ActiveRequestStore, request_id: str
) -> CancelResult:
    """
    Attempt to cancel a request.

    Args:
        request_store: Request store containing active requests
        request_id: ID of request to cancel (from X-Request-ID header)

    Returns:
        CancelResult with status and request_id
    """
    success = request_store.cancel(request_id)

    if success:
        return CancelResult(status=CancelStatus.CANCELLED, request_id=request_id)
    else:
        return CancelResult(status=CancelStatus.NOT_FOUND, request_id=request_id)


def create_cancel_router(
    request_store: ActiveRequestStore,
) -> APIRouter:
    """
    Create federation cancel router.

    Args:
        request_store: Shared request store (same instance as inference router)
    """
    router = APIRouter(prefix="/api/v1/federation", tags=["federation"])

    @router.delete("/inference/{request_id}")
    async def cancel_inference(request_id: str) -> dict[str, str]:
        """
        Cancel a federation request.

        Returns:
            200: {"status": "cancelled" | "not_found", "request_id": "..."}
            500: {"status": "error", "message": "Internal server error"}
        """
        logger.info(f"Cancel request received: {request_id[:8]}...")

        try:
            result = try_cancel_request(request_store, request_id)
            return {
                "status": result.status.value,
                "request_id": result.request_id,
            }

        except asyncio.CancelledError:
            # Preserve cooperative cancellation
            raise

        except Exception:
            logger.exception(f"Cancel operation failed for request {request_id[:8]}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": CancelStatus.ERROR.value,
                    "message": "Internal server error",
                },
            )

    return router
