"""Gateway forwarding endpoint under /gateway/* namespace"""

from fastapi import APIRouter, Depends, Request

from ..dependencies import get_auth_dependency, get_proxy
from ..stargate_core import StargateProxy

router = APIRouter(prefix="/gateway", tags=["gateway-forwarding"])


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def gateway_forward(
    request: Request,
    path: str,
    proxy: StargateProxy = Depends(get_proxy),
    current_user: dict = Depends(get_auth_dependency),
):
    """Forward requests to gateway with /gateway prefix stripped."""
    body = await request.body()

    if request.headers.get("accept") == "text/event-stream":
        return await proxy.forward_streaming_request(
            method=request.method,
            path=f"/{path}",
            headers=dict(request.headers),
            content=body,
            params=dict(request.query_params),
            request=request,  # Pass Request object for disconnection detection
        )
    else:
        return await proxy.forward_request(
            method=request.method,
            path=f"/{path}",
            headers=dict(request.headers),
            content=body,
            params=dict(request.query_params),
        )
