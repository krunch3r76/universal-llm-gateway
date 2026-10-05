"""S6 batch 5 — typed routes for recon/thread/todo sidecar and pinned deliverable writes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch5 import (
    PinnedDeliverableWriteRequest,
    ReconSidecarWriteRequest,
    ThreadSidecarWriteRequest,
    TodoCloseSidecarRequest,
)

router = APIRouter(tags=["substrate-sidecar-writes"])


@router.post("/recon/sidecars", openapi_extra=x_mcp("recon_sidecar_write"))
def recon_sidecar_write_route(body: ReconSidecarWriteRequest) -> dict[str, Any]:
    """Persist a recon markdown sidecar (dispatch ``recon_sidecar_write`` op)."""
    from ..dispatch_ops.ops_misc import _op_recon_sidecar_write

    return _op_recon_sidecar_write(**body.model_dump(exclude_unset=True))


@router.post("/threads/sidecars", openapi_extra=x_mcp("thread_sidecar_write"))
def thread_sidecar_write_route(body: ThreadSidecarWriteRequest) -> dict[str, Any]:
    """Persist a thread markdown sidecar (dispatch ``thread_sidecar_write`` op)."""
    from ..dispatch_ops.ops_misc import _op_thread_sidecar_write

    return _op_thread_sidecar_write(**body.model_dump(exclude_unset=True))


@router.post("/todos/closure-sidecars", openapi_extra=x_mcp("todo_close_sidecar"))
def todo_close_sidecar_route(body: TodoCloseSidecarRequest) -> dict[str, Any]:
    """Write todo closure sidecar + entity pointer (dispatch ``todo_close_sidecar`` op)."""
    from ..dispatch_ops.ops_todos import _op_todo_close_sidecar

    return _op_todo_close_sidecar(**body.model_dump(exclude_unset=True))


@router.post("/pinned-deliverables", openapi_extra=x_mcp("pinned_deliverable_write"))
def pinned_deliverable_write_route(
    body: PinnedDeliverableWriteRequest,
) -> dict[str, Any]:
    """Write a packet-pinned deliverable file (dispatch ``pinned_deliverable_write`` op)."""
    from ..dispatch_ops.ops_misc import _op_pinned_deliverable_write

    return _op_pinned_deliverable_write(**body.model_dump(exclude_unset=True))
