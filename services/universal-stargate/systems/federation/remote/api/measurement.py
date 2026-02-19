"""
VRAM measurement endpoint for Edge Stargate.

Proxies VRAM snapshot requests to Master via federation WebSocket.
Called by Gateway's measurement job to get host-side VRAM readings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from universal_logging import get_logger

if TYPE_CHECKING:
    from ...edge.server import EdgeFederationServer

logger = get_logger(__name__)


def create_measurement_router(
    edge_server: EdgeFederationServer,
) -> APIRouter:
    """Create router for VRAM measurement via federation."""
    router = APIRouter(
        prefix="/api/v1/federation/measurement",
        tags=["federation-measurement"],
    )

    @router.post("/vram")
    async def request_vram_snapshot(
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request host-side VRAM snapshot via Master.

        Body (optional): {"device_index": 0}
        """
        device_index = (body or {}).get("device_index", 0)

        try:
            result = await edge_server.request_vram_snapshot(device_index)
        except TimeoutError:
            raise HTTPException(504, "VRAM measurement timed out waiting for Master")
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))

        if result.get("error"):
            raise HTTPException(502, f"Master measurement error: {result['error']}")

        return {
            "total_mb": result.get("total_mb"),
            "process_count": result.get("process_count"),
        }

    return router
