"""S6 batch 8 — typed routes for entity_retype and endeavor_repair_t1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch8 import EndeavorRepairT1Request, EntityRetypeRequest

router = APIRouter(tags=["substrate-s6-batch8"])


@router.post(
    "/entities/{entity_id}/retype",
    openapi_extra=x_mcp("entity_retype"),
)
def entity_retype_route(
    entity_id: str,
    body: EntityRetypeRequest,
) -> dict[str, Any]:
    """Change entity type and re-prefix id (dispatch ``entity_retype`` op)."""
    from ..dispatch_ops.ops_entities import _op_entity_retype

    return _op_entity_retype(
        entity_id=entity_id,
        **body.model_dump(exclude_unset=True),
    )


@router.post(
    "/endeavors/repair-t1",
    openapi_extra=x_mcp("endeavor_repair_t1"),
)
def endeavor_repair_t1_route(
    body: EndeavorRepairT1Request | None = None,
) -> dict[str, Any]:
    """Apply 5129 T1 endeavor attribute repair (dispatch ``endeavor_repair_t1`` op)."""
    from ..dispatch_ops.ops_endeavor_birth import _op_endeavor_repair_t1

    _ = body  # OpenAPI body slot; handler accepts no persisted fields
    return _op_endeavor_repair_t1()
