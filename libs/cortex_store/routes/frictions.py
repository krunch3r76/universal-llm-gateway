"""Friction list/create/close — typed substrate routes (S6 batch 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from openapi_mcp.binding import x_mcp

from ..models.friction_ops import FrictionCloseRequest, FrictionCreateRequest

router = APIRouter(prefix="/frictions", tags=["frictions"])


@router.get("", openapi_extra=x_mcp("frictions"))
def list_frictions(
    owner: str | None = None,
    owner_type: str | None = None,
    service: str | None = None,
    category: str | None = None,
    seeded_by: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
    anchor_kind: str | None = None,
    anchor_root: str | None = None,
    anchor_seq: int | None = None,
    actionable: bool | None = None,
    since: str | None = None,
    superseded: bool | None = None,
    limit: int | None = None,
    intent: str | None = None,
    include_compaction_pointers: bool = Query(False),
) -> dict[str, Any]:
    """List friction assertions (same shape as dispatch ``frictions`` op)."""
    from ..dispatch_ops.ops_assertions_friction import _op_frictions

    return _op_frictions(
        owner=owner,
        owner_type=owner_type,
        service=service,
        category=category,
        seeded_by=seeded_by,
        charter_root=charter_root,
        window_index=window_index,
        anchor_kind=anchor_kind,
        anchor_root=anchor_root,
        anchor_seq=anchor_seq,
        actionable=actionable,
        since=since,
        superseded=superseded,
        limit=limit,
        intent=intent,
        include_compaction_pointers=include_compaction_pointers,
    )


@router.post("", openapi_extra=x_mcp("friction"))
def create_friction(body: FrictionCreateRequest) -> dict[str, Any]:
    """Log a friction assertion (dispatch ``friction`` op)."""
    from ..dispatch_ops.ops_assertions_friction import _op_friction

    return _op_friction(**body.model_dump(exclude_unset=True))


@router.post("/{assertion_id:int}/close", openapi_extra=x_mcp("friction_close"))
def close_friction_route(
    assertion_id: int,
    body: FrictionCloseRequest,
) -> dict[str, Any]:
    """Close an open friction (dispatch ``friction_close`` op)."""
    from ..dispatch_ops.ops_assertions_write import _op_friction_close

    return _op_friction_close(
        assertion_id=assertion_id,
        **body.model_dump(exclude_unset=True),
    )
