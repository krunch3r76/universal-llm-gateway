"""Admin API for registering aggregate model availability watches.

RAG (and similar clients) POST the model IDs they care about so Stargate emits
``model.available`` / ``model.unavailable`` only for those IDs when the routing
union changes. The response includes the current snapshot after registration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...dependencies import get_auth_dependency, get_proxy

if TYPE_CHECKING:
    from ...stargate_core import StargateProxy

router = APIRouter(prefix="/model-availability", tags=["model-availability"])


class ModelAvailabilityWatchRequest(BaseModel):
    """Request body for registering aggregate availability watches."""

    model_ids: list[str] = Field(
        ...,
        description="Model IDs for which this process wants availability signals.",
    )


@router.post("/watch")
async def post_model_availability_watch(
    body: ModelAvailabilityWatchRequest,
    proxy: StargateProxy = Depends(get_proxy),
    _current_user: dict[str, Any] = Depends(get_auth_dependency),
) -> dict[str, bool]:
    """Register model IDs for aggregate availability events and return snapshot.

    Merges the given IDs into the process-local watch set, runs one reconcile
    so the caller observes current reachability immediately, then returns a map
    of model_id → available under the latest aggregate union catalog.
    """
    emitter = getattr(proxy, "aggregate_availability_emitter", None)
    if emitter is None:
        raise HTTPException(
            status_code=503,
            detail="Aggregate availability emitter not initialized",
        )
    emitter.register_watch(body.model_ids)
    await emitter.reconcile_from_proxy(proxy)
    return emitter.snapshot(body.model_ids)
