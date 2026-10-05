"""POST /assertions/observations — agent observe op (S6 batch 2)."""

from __future__ import annotations

from typing import Any

from openapi_mcp.binding import x_mcp

from ...models.friction_ops import ObserveRequest
from ._shared import router


@router.post("/observations", openapi_extra=x_mcp("observe"))
def observe_assertion(body: ObserveRequest) -> dict[str, Any]:
    """Record an agent observation (dispatch ``observe`` op)."""
    from ...dispatch_ops.ops_assertions_write import _op_observe

    return _op_observe(**body.model_dump(exclude_unset=True))
