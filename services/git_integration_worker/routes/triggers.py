"""Trigger schedule CRUD routes — Bearer-gated like agent-bus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agent_bus_store.auth import require_token
from fastapi import APIRouter, Depends, HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel, Field
from universal_logging import get_logger

from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.trigger_service.db import as_utc
from services.git_integration_worker.trigger_service.fire import project_ask_configured
from services.git_integration_worker.trigger_service.models import TriggerStoreError
from services.git_integration_worker.trigger_service.store import TriggerStore
from services.git_integration_worker.trigger_service.story_envelope import (
    stamp_trigger_envelope,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/triggers", tags=["triggers"])


class ScheduleTriggerRequest(BaseModel):
    """POST /api/v1/triggers body — absolute or delay fire plus optional predicate fields.

    When ``predicate`` is set, ``expires_at`` is required for ``trigger_terminal``;
    optional for ``fleet_idle``. Catalog validation refuses unknown types /
    unresolvable upstream ``trigger_id`` with stable ``reason_code`` values
    (see ``TriggerStore.schedule``).
    """

    created_by: str = "life-seat"
    fire_at: datetime | None = None
    delay_s: float | None = Field(default=None, ge=0)
    prompt_uri: str | None = None
    prompt_text: str | None = None
    purpose: str = "operator-proxy"
    model: str = "opus-5"
    arc: str | None = None
    so_what: str | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    predicate: str | None = None
    predicate_args: dict | None = None
    expires_at: datetime | None = None
    recur_every_s: int | None = Field(default=None, ge=1)
    require_act_receipt: int | None = Field(
        default=None,
        description=(
            "Tri-state: 1=require, 0=waive, null=derive from purpose at verify-time"
        ),
    )
    charter_root: str | None = None
    window_index: int | None = Field(default=None, ge=0)


def _store() -> TriggerStore:
    return TriggerStore()


def _resolve_fire_at(req: ScheduleTriggerRequest) -> datetime:
    if req.fire_at is not None:
        return as_utc(req.fire_at)
    if req.delay_s is not None:
        return datetime.now(UTC) + timedelta(seconds=req.delay_s)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="fire_at or delay_s required",
    )


def _refuse_misconfigured_schedule() -> None:
    if project_ask_configured():
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "reason_code": "project_ask_unconfigured",
            "reason": (
                "PROJECT_ASK_URL not configured — cannot honour scheduled fires"
            ),
        },
    )


@router.post(
    "",
    dependencies=[Depends(require_token)],
    openapi_extra=x_mcp("schedule", tool="trigger"),
)
async def schedule_trigger(req: ScheduleTriggerRequest) -> dict[str, Any]:
    """Schedule a future operator-proxy fire (fail-closed when misconfigured)."""
    _refuse_misconfigured_schedule()
    if not req.prompt_uri and not (req.prompt_text and req.prompt_text.strip()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="prompt_uri or prompt_text required",
        )
    fire_at = _resolve_fire_at(req)
    store = _store()
    try:
        row = store.schedule(
            created_by=req.created_by,
            fire_at=fire_at,
            prompt_uri=req.prompt_uri,
            prompt_text=req.prompt_text,
            purpose=req.purpose,
            model=req.model,
            arc=req.arc,
            so_what=req.so_what,
            max_attempts=req.max_attempts,
            predicate=req.predicate,
            predicate_args=req.predicate_args,
            expires_at=req.expires_at,
            recur_every_s=req.recur_every_s,
            require_act_receipt=req.require_act_receipt,
            charter_root=req.charter_root,
            window_index=req.window_index,
            _require_act_explicit=req.require_act_receipt is not None,
        )
    except TriggerStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason_code": exc.code,
                "reason": str(exc),
            },
        ) from exc
    scheduled_payload: dict[str, Any] = {
        "trigger_id": row.id,
        "fire_at": row.fire_at,
        "arc": row.arc,
        "created_by": row.created_by,
    }
    stamp_trigger_envelope(scheduled_payload, row)
    publish_lib_signal("giw.trigger.scheduled", scheduled_payload)
    return row.to_dict()


@router.get(
    "",
    dependencies=[Depends(require_token)],
    openapi_extra=x_mcp("list", tool="trigger", readonly=True),
)
async def list_triggers(limit: int = 100) -> dict[str, Any]:
    store = _store()
    rows = store.list_triggers(limit=min(limit, 500))
    return {"triggers": [r.to_dict() for r in rows], "count": len(rows)}


@router.get(
    "/{trigger_id}",
    dependencies=[Depends(require_token)],
    openapi_extra=x_mcp("get", tool="trigger", readonly=True),
)
async def get_trigger(trigger_id: str) -> dict[str, Any]:
    store = _store()
    row = store.get(trigger_id)
    if row is None:
        raise HTTPException(status_code=404, detail="trigger not found")
    return row.to_dict()


@router.post("/{trigger_id}/revoke", dependencies=[Depends(require_token)])
async def revoke_trigger(trigger_id: str) -> dict[str, Any]:
    """Stop future fires for a recurring trigger (works when status is fired)."""
    store = _store()
    try:
        row = store.revoke(trigger_id)
    except TriggerStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    revoked_payload: dict[str, Any] = {
        "trigger_id": row.id,
        "status": row.status,
        "recur_every_s": row.recur_every_s,
    }
    stamp_trigger_envelope(revoked_payload, row)
    publish_lib_signal("giw.trigger.revoked", revoked_payload)
    return row.to_dict()


@router.delete(
    "/{trigger_id}",
    dependencies=[Depends(require_token)],
    openapi_extra=x_mcp("cancel", tool="trigger"),
)
async def cancel_trigger(trigger_id: str) -> dict[str, Any]:
    store = _store()
    try:
        row = store.cancel(trigger_id)
    except TriggerStoreError as exc:
        if exc.code == "trigger_already_fired":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": exc.code,
                    "reason": str(exc),
                },
            ) from exc
        if exc.code == "trigger_firing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": exc.code,
                    "reason": str(exc),
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    cancelled_payload: dict[str, Any] = {
        "trigger_id": row.id,
        "cancelled_at": row.cancelled_at,
    }
    stamp_trigger_envelope(cancelled_payload, row)
    publish_lib_signal("giw.trigger.cancelled", cancelled_payload)
    return row.to_dict()
