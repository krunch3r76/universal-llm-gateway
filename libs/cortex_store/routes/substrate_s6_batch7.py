"""S6 batch 7 — typed route for register_skill_substrate (view_render withheld batch 8)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch7 import RegisterSkillSubstrateRequest

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
