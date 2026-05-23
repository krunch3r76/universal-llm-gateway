"""Cortex dispatch endpoint — POST /dispatch {tool, arguments}.

Unified entry for MCP `cortex` tool and libs/agent_seat injected tool calls.
Eliminates per-caller op-registry drift by routing all op dispatch through a
single registry in libs/cortex_store/dispatch_ops/.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel
from universal_logging import get_logger

from ..dispatch_ops import execute_op

logger = get_logger("cortex-api.dispatch")
router = APIRouter(tags=["dispatch"])


class DispatchRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] | str | None = None


@router.post("/dispatch")
def dispatch(req: DispatchRequest) -> Any:
    """Execute a named cortex op. Body: {tool, arguments}.

    arguments accepts either a JSON object or a JSON-encoded string (xAI
    remote-MCP emits objects; legacy agent_seat callers emit strings).
    """
    return execute_op(req.tool, req.arguments if req.arguments is not None else {})
