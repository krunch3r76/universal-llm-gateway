"""``GET /health`` — readiness probe for git-integration-worker."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["git-integration-health"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Return minimal health envelope for manage ``wait_healthy`` probes."""
    version = getattr(request.app.state, "worker_version", "unknown")
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "service": "git-integration-worker",
            "version": version,
        },
    )
