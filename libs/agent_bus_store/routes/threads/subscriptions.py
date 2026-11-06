"""HTTP routes for endpoint subscribe, unsubscribe, and list."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from openapi_mcp.binding import x_mcp
from pydantic import BaseModel, model_validator

from ...db.endpoint_subscriptions import (
    ChatgptWorkCasError,
    SubscribeError,
    attach_instance_wake_head,
    list_subscriptions,
    subscribe_endpoint,
    unsubscribe_endpoint,
)
from ...db.endpoint_wake_ledger import (
    resolve_inform_delivery_binding,
    resolve_steer_delivery_binding,
    resolve_wake_delivery_binding,
)
from ...events.endpoint_subscribed import (
    emit_endpoint_subscribed,
    emit_endpoint_unsubscribed,
)
from . import router as threads_router

router = threads_router


class SubscribeBody(BaseModel):
    agent: str
    thread_scope: str
    surface: str | None = None
    display: str | None = None
    title: str | None = None
    agent_id: str | None = None
    bound_by: str | None = None
    evidence: str | None = None
    actuation: str | None = None
    home_house_id: str | None = None
    conversation_url: str | None = None
    expected: str | None = None
    attempt_id: str | None = None
    handoff_sha256: str | None = None
    instance_id: str | None = None

    @model_validator(mode="after")
    def _require_surface_unless_chatgpt_work(self) -> SubscribeBody:
        actuation = str(self.actuation or "").strip().lower()
        if actuation == "chatgpt_work":
            return self
        if not str(self.surface or "").strip() or not str(self.display or "").strip():
            raise ValueError("endpoint_surface_display_required")
        return self


class UnsubscribeBody(BaseModel):
    agent: str
    thread_scope: str
    bound_by: str | None = None
    evidence: str | None = None
    expected_agent_id: str | None = None


def _refuse(exc: SubscribeError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": exc.code},
    )


def _refuse_work(exc: ChatgptWorkCasError) -> HTTPException:
    code = exc.code
    if code == "seat_cas_conflict":
        status_code = status.HTTP_409_CONFLICT
    elif code == "endpoint_thread_missing":
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(status_code=status_code, detail={"code": code})


@router.post(
    "/endpoint-subscriptions",
    openapi_extra=x_mcp("subscribe", tool="agent_bus"),
)
async def subscribe_route(body: SubscribeBody) -> dict[str, Any]:
    """Append a subscription row when the endpoint identity changes."""
    actuation = str(body.actuation or "").strip().lower()
    if actuation == "chatgpt_work":
        try:
            return attach_instance_wake_head(
                home_house_id=str(body.home_house_id or body.thread_scope or ""),
                conversation_url=str(body.conversation_url or ""),
                expected=str(body.expected or ""),
                attempt_id=str(body.attempt_id or ""),
                handoff_sha256=str(body.handoff_sha256 or ""),
                bound_by=str(body.bound_by or ""),
                evidence=str(body.evidence or ""),
                instance_id=body.instance_id,
            )
        except ChatgptWorkCasError as exc:
            raise _refuse_work(exc) from exc
    try:
        payload = body.model_dump(
            exclude={
                "actuation",
                "home_house_id",
                "conversation_url",
                "expected",
                "attempt_id",
                "handoff_sha256",
            }
        )
        result = subscribe_endpoint(**payload)
    except SubscribeError as exc:
        raise _refuse(exc) from exc
    if result["appended"]:
        row = result["row"]
        prior = result["prior"] or {}
        emit_endpoint_subscribed(
            agent=row["agent"],
            thread_scope=row["thread_scope"],
            surface=row["surface"],
            display=row["display"],
            title=row["title"],
            agent_id=row["agent_id"],
            subscription_id=row["id"],
            prior_subscription_id=prior.get("id"),
            prior_surface=prior.get("surface"),
            prior_display=prior.get("display"),
            prior_title=prior.get("title"),
            prior_agent_id=prior.get("agent_id"),
            bound_by=row["bound_by"],
        )
    return result


@router.post(
    "/endpoint-subscriptions/unsubscribe",
    openapi_extra=x_mcp("unsubscribe", tool="agent_bus"),
)
async def unsubscribe_route(body: UnsubscribeBody) -> dict[str, Any]:
    """Append an unsubscribe tombstone for the current row."""
    result = unsubscribe_endpoint(**body.model_dump())
    if result["appended"]:
        row = result["row"]
        prior = result["prior"] or {}
        emit_endpoint_unsubscribed(
            agent=row["agent"],
            thread_scope=row["thread_scope"],
            subscription_id=row["id"],
            prior_subscription_id=prior.get("id"),
            bound_by=row["bound_by"],
        )
    return result


@router.get(
    "/endpoint-subscriptions",
    openapi_extra=x_mcp("subscriptions", tool="agent_bus"),
)
async def subscriptions_route(
    agent: str | None = None, thread_scope: str | None = None
) -> dict[str, Any]:
    """List the current subscription row per (agent, thread_scope)."""
    return {"subscriptions": list_subscriptions(agent, thread_scope)}


@router.get(
    "/delivery-bindings/resolve",
    openapi_extra=x_mcp("delivery_bindings_resolve", tool="agent_bus"),
)
async def delivery_bindings_resolve_route(
    house_id: str,
    intent: str,
    agent: str | None = None,
) -> dict[str, Any]:
    """Resolve delivery actuation for a house and intent (wake, steer, or inform)."""
    intent_norm = str(intent or "").strip().lower()
    if intent_norm == "wake":
        return resolve_wake_delivery_binding(house_id=house_id, agent=agent)
    if intent_norm == "steer":
        return resolve_steer_delivery_binding(house_id=house_id)
    if intent_norm == "inform":
        return resolve_inform_delivery_binding(house_id=house_id)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "delivery_binding_intent_unsupported"},
    )
