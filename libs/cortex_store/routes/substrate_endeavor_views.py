"""S6 batch 6 — typed routes for endeavor strategy rows and view render."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch6 import (
    EndeavorDisposeRowRequest,
    EndeavorWriteRowRequest,
    ViewRenderRequest,
)

router = APIRouter(tags=["substrate-endeavor-views"])


@router.post("/endeavors/strategy-rows", openapi_extra=x_mcp("endeavor_write_row"))
def endeavor_write_row_route(body: EndeavorWriteRowRequest) -> dict[str, Any]:
    """Write an endeavor strategy row (dispatch ``endeavor_write_row`` op)."""
    from ..dispatch_ops.ops_endeavor_birth import _op_endeavor_write_row

    return _op_endeavor_write_row(**body.model_dump(exclude_unset=True))


@router.post(
    "/endeavors/strategy-rows/dispose",
    openapi_extra=x_mcp("endeavor_dispose_row"),
)
def endeavor_dispose_row_route(body: EndeavorDisposeRowRequest) -> dict[str, Any]:
    """Dispose a pending endeavor strategy row (dispatch ``endeavor_dispose_row`` op)."""
    from ..dispatch_ops.ops_endeavor_birth import _op_endeavor_dispose_row

    return _op_endeavor_dispose_row(**body.model_dump(exclude_unset=True))


@router.post("/views/{document_id}/render", openapi_extra=x_mcp("view_render"))
def view_render_route(
    document_id: str,
    body: ViewRenderRequest,
) -> dict[str, Any]:
    """Render or refresh a derived view document (dispatch ``view_render`` op).

    POST carries mutating modes and ``narrative_sections``; read-asof uses
    ``mode=read_asof`` with ``as_of_system`` (bind proposed GET+asof query).
    """
    from ..dispatch_ops.ops_views import _op_view_render

    return _op_view_render(
        document_id=document_id,
        **body.model_dump(exclude_unset=True),
    )
