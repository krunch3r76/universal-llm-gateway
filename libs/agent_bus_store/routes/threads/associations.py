"""Lane/branch association routes: branch-associate, branch-current, lane-bind, lane-current."""

from __future__ import annotations

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp

from ...db import (
    associate_branch,
    get_current_branch,
    normalize_thread_id,
    reject_client_ordering_tokens,
)
from ...db.branch_associations import ClientOrderingTokenError
from ...db.lane_associations import (
    ClientOrderingTokenError as LaneClientOrderingTokenError,
)
from ...db.lane_associations import (
    LaneBindCreate,
    LaneBindResponse,
    LaneCurrentResponse,
    associate_lane,
    get_current_lane,
    invalid_lane_role_envelope,
)
from ...db.lane_associations import (
    reject_client_ordering_tokens as reject_lane_ordering_tokens,
)
from ...turns_models import (
    BranchAssociateCreate,
    BranchAssociateResponse,
    BranchCurrentResponse,
)
from . import router


@router.post(
    "/threads/{thread_id}/branch-associate",
    response_model=BranchAssociateResponse,
    openapi_extra=x_mcp("branch_associate", tool="agent_bus"),
)
async def branch_associate_route(
    thread_id: str,
    body: BranchAssociateCreate,
) -> BranchAssociateResponse:
    """Append one lane↔branch association; current is derived from MAX(id)."""
    thread_id = normalize_thread_id(thread_id)
    try:
        reject_client_ordering_tokens(body.model_dump())
        result = associate_branch(thread_id=thread_id, branch_name=body.branch_name)
    except ClientOrderingTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "client_ordering_token", "reason": str(exc)},
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_branch_name", "reason": str(exc)},
        )
    return BranchAssociateResponse(**result)


@router.get(
    "/threads/{thread_id}/branch-current",
    response_model=BranchCurrentResponse,
    openapi_extra=x_mcp("branch_current", tool="agent_bus"),
)
async def branch_current_route(thread_id: str) -> BranchCurrentResponse:
    """Return derived current branch for a lane from association history."""
    thread_id = normalize_thread_id(thread_id)
    try:
        result = get_current_branch(thread_id=thread_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return BranchCurrentResponse(**result)


@router.post(
    "/threads/{thread_id}/lane-bind",
    response_model=LaneBindResponse,
    openapi_extra=x_mcp("lane_bind", tool="agent_bus"),
)
async def lane_bind_route(
    thread_id: str,
    body: LaneBindCreate,
) -> LaneBindResponse:
    """Append one lane parentage association; current is derived from MAX(id)."""
    thread_id = normalize_thread_id(thread_id)
    try:
        reject_lane_ordering_tokens(body.model_dump())
        result = associate_lane(
            thread_id=thread_id,
            parent_thread_id=body.parent_thread_id,
            lane_role=body.lane_role,
            bound_by=body.bound_by,
            evidence=body.evidence,
        )
    except LaneClientOrderingTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "client_ordering_token", "reason": str(exc)},
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    except ValueError as exc:
        msg = str(exc)
        if "lane_role" in msg:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=invalid_lane_role_envelope(
                    lane_role=body.lane_role,
                    reason=msg,
                ),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": msg, "reason": "invalid_lane_bind"},
        ) from exc
    return LaneBindResponse(**result)


@router.get(
    "/threads/{thread_id}/lane-current",
    response_model=LaneCurrentResponse,
    openapi_extra=x_mcp("lane_current", tool="agent_bus"),
)
async def lane_current_route(thread_id: str) -> LaneCurrentResponse:
    """Return derived current lane parentage for a thread from association history."""
    thread_id = normalize_thread_id(thread_id)
    try:
        result = get_current_lane(thread_id=thread_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found",
        )
    return LaneCurrentResponse(**result)
