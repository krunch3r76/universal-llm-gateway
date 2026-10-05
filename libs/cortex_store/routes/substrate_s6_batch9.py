"""S6 batch 9 — typed route for view_render."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch9 import ViewRenderRequest

router = APIRouter(tags=["substrate-s6-batch9"])


@router.post(
    "/views/{document_id}/render",
    openapi_extra=x_mcp("view_render"),
)
def view_render_route(
    document_id: str,
    body: ViewRenderRequest,
) -> dict[str, Any]:
    """Render or refresh a derived view document (dispatch ``view_render`` op)."""
    from ..dispatch_ops.ops_views import _op_view_render

    return _op_view_render(
        document_id=document_id,
        **body.model_dump(exclude_unset=True),
    )
