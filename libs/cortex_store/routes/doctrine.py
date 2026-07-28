"""Doctrine routes — served posture-stack projections."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..dispatch_ops._shared import _FILES_ROOT
from ..vision_digest import VisionDigest, build_vision_digest

router = APIRouter(prefix="/api/v1/doctrine", tags=["doctrine"])


@router.get(
    "/vision-digest",
    response_model=VisionDigest,
    status_code=status.HTTP_200_OK,
    operation_id="getVisionDigest",
)
def get_vision_digest() -> VisionDigest:
    """Return the live posture-stack MAP digest with verbatim pillar law."""
    try:
        return build_vision_digest(_FILES_ROOT)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
