"""S6 batch 7 — typed routes for register_skill_substrate and view_render."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch7 import RegisterSkillSubstrateRequest, ViewRenderRequest

router = APIRouter(tags=["substrate-s6-batch7"])


@router.post(
    "/skills/register-substrate",
    openapi_extra=x_mcp("register_skill_substrate"),
)
def register_skill_substrate_route(
    body: RegisterSkillSubstrateRequest,
) -> dict[str, Any]:
    """Register skill substrate composite (dispatch ``register_skill_substrate`` op)."""
    from ..dispatch_ops.ops_composites import _op_register_skill_substrate

    return _op_register_skill_substrate(**body.model_dump(exclude_unset=True))


@router.post(
    "/views/{document_id}/render",
    openapi_extra=x_mcp("view_render"),
)
def view_render_route(
    document_id: str,
    body: ViewRenderRequest,
) -> dict[str, Any]:
    """Render or refresh a derived view (dispatch ``view_render`` op).

    POST because the handler may write files, relationships, and events on
    register/refresh/full modes; ``mode`` defaults to ``refresh`` like the handler.
    """
    from ..dispatch_ops.ops_views import _op_view_render

    payload = body.model_dump(exclude_unset=True)
    payload["document_id"] = document_id
    return _op_view_render(**payload)
