"""Cortex dispatch endpoint — POST /dispatch {tool, arguments}.

Unified entry for MCP `cortex` tool and libs/agent_seat injected tool calls.
Eliminates per-caller op-registry drift by routing all op dispatch through a
single registry in libs/cortex_store/dispatch_ops/.

Death-path gate (A3): delete this route ONLY when served-parity bijection holds
AND zero non-adapter /dispatch traffic after the telemetry window. See
``cortex_store.openapi_mcp.death_path.DEATH_PATH_GATE_DOC``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from universal_logging import get_logger

from ..dispatch_ops import execute_op
from ..openapi_mcp.death_path import DEATH_PATH_GATE_DOC

logger = get_logger("cortex-api.dispatch")
router = APIRouter(tags=["dispatch"])


class DispatchRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] | str | None = None
    surface: str | None = None
    seat: str | None = None
    via_adapter: bool | None = None


@router.post("/dispatch")
def dispatch(req: DispatchRequest) -> Any:
    """Execute a named cortex op. Body: {tool, arguments}.

    arguments accepts either a JSON object or a JSON-encoded string (xAI
    remote-MCP emits objects; legacy agent_seat callers emit strings).

    Optional telemetry fields (surface, seat, via_adapter) are relayed from the
    MCP adapter for per-op × per-seat observability.
    """
    logger.debug(DEATH_PATH_GATE_DOC.splitlines()[0])
    return execute_op(
        req.tool,
        req.arguments if req.arguments is not None else {},
        surface=req.surface,
        seat=req.seat,
        via_adapter=req.via_adapter,
    )
