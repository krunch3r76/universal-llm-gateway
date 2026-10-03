"""Pre-restart gateway membership snapshot for manage settle probes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...dependencies import get_auth_dependency, get_proxy
from ...stargate.runtime.component_factory.pipeline_registry_bootstrap import (
    emit_gateway_membership,
    membership_payload,
)

router = APIRouter(tags=["admin"])


@router.post("/admin/emit-gateway-membership")
async def emit_gateway_membership_admin(
    proxy=Depends(get_proxy),
    _current_user: dict[str, object] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Publish ``federation.gateway.membership`` before a supervised restart."""
    payload = membership_payload(proxy)
    await emit_gateway_membership(proxy)
    return JSONResponse(
        status_code=200,
        content={"signal": "federation.gateway.membership", **payload},
    )
