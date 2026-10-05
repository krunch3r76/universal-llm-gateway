"""GET /assertions/{assertion_id} — single assertion read (assertion_get op)."""

from __future__ import annotations

from typing import Any

from openapi_mcp.binding import x_mcp

from ._shared import router


@router.get("/{assertion_id}", openapi_extra=x_mcp("assertion_get"))
def get_assertion(assertion_id: int) -> dict[str, Any]:
    """Read one assertion by id (full AssertionItem shape)."""
    from ...dispatch_ops.ops_assertions_update import _op_assertion_get

    return _op_assertion_get(assertion_id=assertion_id)
